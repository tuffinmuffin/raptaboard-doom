//! The FRC 2026 field in Doom as a raptaboard tab plugin: room4doom's game and
//! software renderer, run by raptaboard instead of a window.
//!
//! The game steps 35 times a second by the frame's clock and is drawn about
//! 60 times a second between steps (room4doom interpolates), as a pixel
//! frame. Keys come while the tab has the keyboard. The WADs are read from
//! the plugin's own folder (`/pack`). Cheats work as in the standalone game.
//!
//! It opens on a title card (`splash.rgba` from the plugin's folder, made by
//! tools/splash.py) and loads the game when clicked (or on Enter or Space).
//! Quitting from the game's menu ends the game, gives the keyboard and mouse
//! back to raptaboard and shows the title card again; a click then starts a new
//! game in the data already loaded (freeing it would take about half a second).
//! Sound isn't mixed yet: the game's sound messages are dropped.

mod cheats;
mod display;
mod keys;
mod settings;

use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver};

use doom_ui::{Finale, GameMenu, Intermission, Messages, Statusbar};
use game_config::GameOptions;
use gamestate::Game;
use gamestate::subsystems::GameSubsystem;
use gamestate_traits::{KeyCode, SubsystemTrait};
use input::InputState;
use raptaboard_plugin::prelude::*;
use render_backend::{RenderStack, RenderType};
use sound_common::SoundAction;
use wad::WadData;

use display::Machines;

/// Steps (tics) a second, Doom's.
const TICS_PER_S: f64 = 35.0;
/// The most steps run in one frame, so a long pause (a closed tab) doesn't
/// fast-forward the game: the rest are skipped.
const MOST_TICS_A_FRAME: u64 = 4;
/// Drawn this often while the tab is open, for smooth motion between steps.
const FRAME_S: f64 = 1.0 / 60.0;
/// The IWAD, then the PWADs on it, from the plugin's folder.
const IWAD: &str = "freedoom2.wad";
const PWADS: [&str; 3] = ["frc2026.wad", "robots.wad", "face.wad"];

/// The game, once started.
struct Running {
    game: Game,
    input: InputState,
    menu: GameMenu,
    machines: Machines,
    screen: RenderStack<u32>,
    /// The game's sound messages (not mixed yet).
    sound: Receiver<SoundAction>,
    /// Steps run so far, against the clock.
    tics: u64,
    /// The game's menu showed after the last step.
    menu_open: bool,
    /// The game's settings, as the Options menu last left them.
    settings: settings::Values,
    /// Frames drawn in the second since `fps_since_s`, and the last second's
    /// count as shown ("FPS 60") when Show FPS is on.
    frames: u32,
    fps_since_s: f64,
    fps_text: String,
    /// The display the game draws for (`display_size`): its shape is the
    /// frame's on screen.
    display: (u32, u32),
    /// The frame's time the game started at.
    started_s: f64,
    /// Doom keys held, so all can be let go when the tab loses the keyboard.
    held: HashSet<KeyCode>,
    /// The cheat codes typed so far (`iddqd`).
    cheats: cheats::Cheats,
    /// The mouse's movement since the last step (raptaboard may send several
    /// between steps; the game takes one each step).
    mouse: (f32, f32),
    /// Mouse buttons held, let go with the keyboard.
    buttons: HashSet<gamestate_traits::MouseBtn>,
}

#[derive(Default)]
struct Doom {
    running: Option<Running>,
    /// Why the game couldn't start.
    failed: Option<String>,
    /// Clicked on the title card: start at the next render.
    start: bool,
    /// Quit from the game's menu: the title card shows, the game kept loaded.
    quit: bool,
    /// The title card, read once: width, height, RGBA (`None` if missing).
    splash: Option<Option<(u32, u32, Vec<u8>)>>,
}

/// The title card from the plugin's folder: `splash.rgba`, width and height
/// as little-endian u32, then RGBA rows (tools/splash.py).
fn read_splash() -> Option<(u32, u32, Vec<u8>)> {
    let bytes = raptaboard_plugin::pack_file("splash.rgba").ok()?;
    let w = u32::from_le_bytes(bytes.get(0..4)?.try_into().ok()?);
    let h = u32::from_le_bytes(bytes.get(4..8)?.try_into().ok()?);
    let rgba = bytes.get(8..)?.to_vec();
    (rgba.len() == w as usize * h as usize * 4).then_some((w, h, rgba))
}

fn start(time_s: f64, width: f32, height: f32) -> Result<Running, String> {
    let pack = PathBuf::from(raptaboard_plugin::PACK_DIR);
    for name in std::iter::once(IWAD).chain(PWADS) {
        if !pack.join(name).is_file() {
            return Err(format!("{name} isn't in the plugin's folder"));
        }
    }
    let options = GameOptions {
        iwad: pack.join(IWAD).display().to_string(),
        pwad: PWADS
            .iter()
            .map(|p| pack.join(p).display().to_string())
            .collect(),
        map: 1,
        warp: true,
        hi_res: false,
        ..GameOptions::default()
    };
    let wad = WadData::new(&pack.join(IWAD));
    let (options, wad) = gamestate::prepare_wad(options, wad);
    let (sound_tx, sound) = mpsc::channel();
    let settings = settings::defaults();
    let display = display_size(width, height);
    let mut game = Game::new(options, wad, sound_tx, None, settings);
    let machines = GameSubsystem {
        statusbar: Statusbar::new(game.game_type.mode, &game.wad_data),
        intermission: Intermission::new(game.game_type.mode, &game.wad_data, &game.umapinfo),
        hud_msgs: Messages::new(&game.wad_data),
        finale: Finale::new(&game.wad_data),
    };
    let mut menu = GameMenu::new(game.game_type.mode, &game.wad_data);
    menu.init(&game);
    menu.set_render_context(false, false);
    let mut screen = RenderStack::new(
        settings::resolution(&settings),
        render_backend::new_buffer(display.0, display.1),
        RenderType::Software,
    );
    screen.set_statusbar_height(settings::statusbar_height(&settings));
    let mut input = InputState::new((&keys::controls()).into());
    settings::apply(&settings, &mut game, &mut screen, &mut input);
    Ok(Running {
        game,
        input,
        menu,
        machines,
        screen,
        sound,
        tics: 0,
        menu_open: false,
        settings,
        frames: 0,
        fps_since_s: time_s,
        fps_text: String::new(),
        started_s: time_s,
        display,
        held: HashSet::new(),
        cheats: cheats::Cheats::new(),
        mouse: (0.0, 0.0),
        buttons: HashSet::new(),
    })
}

/// An ARGB frame as RGBA bytes.
fn rgba(argb: &[u32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(argb.len() * 4);
    for p in argb {
        out.extend([(p >> 16) as u8, (p >> 8) as u8, *p as u8, 255]);
    }
    out
}

/// The display the game draws for: the tab, but never narrower than 4:3.
/// The game's frame is as wide as the display's shape makes it (its pixels
/// tall, as on a CRT): narrower than 4:3, it's under 320 pixels and Doom's
/// 320-wide status bar and menu are squeezed into it, dropping columns.
fn display_size(width: f32, height: f32) -> (u32, u32) {
    let h = height.max(1.0).round();
    let w = width.round().max((h * 4.0 / 3.0).ceil());
    (w as u32, h as u32)
}

/// Where the frame goes: as large as fits in `frame` and centered, at the
/// `aspect` of the display it was drawn for (its pixels aren't square: the
/// frame's own width over height isn't its shape).
fn fit(frame: &FrameInfo, aspect: f32) -> (Point, Point) {
    let (fw, fh) = (frame.size.width, frame.size.height);
    let (dw, dh) = if fw / fh > aspect {
        (fh * aspect, fh)
    } else {
        (fw, fw / aspect)
    };
    let min = Point {
        x: (fw - dw) / 2.0,
        y: (fh - dh) / 2.0,
    };
    (
        min,
        Point {
            x: min.x + dw,
            y: min.y + dh,
        },
    )
}

/// A raptaboard mouse button as Doom's: left fires, right moves forward,
/// middle strafes (room4doom's controls).
fn mouse_button(b: MouseButton) -> Option<gamestate_traits::MouseBtn> {
    use gamestate_traits::MouseBtn as M;
    Some(match b {
        MouseButton::Primary => M::Left,
        MouseButton::Secondary => M::Right,
        MouseButton::Middle => M::Middle,
        _ => return None,
    })
}

/// What a screen reader says for the game (and tests read): the player's
/// health and armor, and god mode when it's on.
fn summary(game: &Game) -> String {
    let player = &game.players[game.consoleplayer];
    let status = &player.status;
    // In a level the health is its map object's; between levels, the status's.
    let health = player.mobj().map_or(status.health, |m| m.health);
    let god = status.cheats.contains(gameplay::PlayerCheat::Godmode);
    // The weapon in hand's ammo (none for the fist and chainsaw).
    let ammo = gameplay::WEAPON_INFO
        .get(status.readyweapon as usize)
        .and_then(|w| status.ammo.get(w.ammo as usize))
        .map(|n| format!(", ammo {n}"))
        .unwrap_or_default();
    format!(
        "Doom: health {}, armor {}{ammo}{}",
        health,
        status.armorpoints,
        if god { ", god mode" } else { "" }
    )
}

impl Widget for Doom {
    fn subscriptions(&self) -> Vec<Subscription> {
        Vec::new()
    }

    fn render(&mut self, frame: FrameInfo) -> Picture {
        let mut picture = Picture::empty();
        // Clicked after a Quit: a new game in the data already loaded.
        if self.start
            && self.quit
            && let Some(r) = &mut self.running
        {
            use gamestate_traits::GameTraits as _;
            let skill = r.game.options.skill;
            r.game.defered_init_new(skill, 1, 1);
            r.game.set_running(true);
            r.started_s = frame.time_s;
            r.tics = 0;
            self.quit = false;
            self.start = false;
        }
        // The title card, until clicked (and after Quit).
        if (self.quit || self.running.is_none()) && !self.start && self.failed.is_none() {
            let splash = self.splash.get_or_insert_with(read_splash);
            match splash {
                Some((w, h, rgba)) => {
                    let aspect = *w as f32 / *h as f32;
                    let (fw, fh) = (frame.size.width, frame.size.height);
                    let (dw, dh) = if fw / fh > aspect {
                        (fh * aspect, fh)
                    } else {
                        (fw, fw / aspect)
                    };
                    let min = Point {
                        x: (fw - dw) / 2.0,
                        y: (fh - dh) / 2.0,
                    };
                    picture.shape(Shape::Pixels(Pixels {
                        min,
                        max: Point {
                            x: min.x + dw,
                            y: min.y + dh,
                        },
                        width: *w,
                        height: *h,
                        rgba: rgba.clone(),
                        smooth: true,
                    }));
                }
                None => {
                    picture.text(centered_text(&frame, "Doom: click to play", 20.0));
                }
            }
            picture.summary = Some("Doom: click to play".into());
            return picture;
        }
        if self.running.is_none() && self.failed.is_none() {
            self.start = false;
            match start(frame.time_s, frame.size.width, frame.size.height) {
                Ok(r) => self.running = Some(r),
                Err(e) => {
                    host::log(&e);
                    self.failed = Some(e);
                }
            }
        }
        let Some(r) = &mut self.running else {
            let why = self.failed.clone().unwrap_or_default();
            picture.text(centered_text(
                &frame,
                &format!("Doom can't start: {why}"),
                16.0,
            ));
            return picture;
        };
        // The steps due by the clock, at most a few a frame.
        let clock = (frame.time_s - r.started_s).max(0.0) * TICS_PER_S;
        let due = clock.floor() as u64;
        if due > r.tics + MOST_TICS_A_FRAME {
            r.tics = due - MOST_TICS_A_FRAME;
        }
        while r.tics < due {
            // The mouse's movement since the last step, as room4doom's window
            // loop gives it (its acceleration, then sensitivity).
            if r.mouse != (0.0, 0.0) {
                let ev = &mut r.input.events;
                let x = ev.apply_mouse_accel(r.mouse.0) as i32;
                let y = ev.apply_mouse_accel(r.mouse.1) as i32;
                ev.apply_mouse_sensitivity((x, y));
                r.mouse = (0.0, 0.0);
            }
            r.menu_open =
                display::run_game_tic(&mut r.game, &mut r.input, &mut r.menu, &mut r.machines);
            r.tics += 1;
        }
        // Quit from the game's menu: end the game (its memory freed), give
        // the keyboard and mouse back, and show the title card.
        if !r.game.running() {
            for kc in r.held.drain() {
                r.input.events.unset_kb(kc);
            }
            for b in r.buttons.drain() {
                r.input.events.unset_mb(b);
            }
            self.quit = true;
            host::release_focus();
            return self.render(frame);
        }
        // Not mixed yet.
        while r.sound.try_recv().is_ok() {}
        // A setting the menu changed: applied before this frame is drawn.
        if let Some(v) = settings::changed(&mut r.game) {
            settings::apply(&v, &mut r.game, &mut r.screen, &mut r.input);
            r.settings = v;
        }
        // Detail or Native UI changed during a screen wipe: now it's done.
        settings::reconfigure(&r.settings, &mut r.screen);
        let frac = if settings::interpolate(&r.settings) {
            (clock - clock.floor()) as f32
        } else {
            1.0
        };
        r.frames += 1;
        if frame.time_s - r.fps_since_s >= 1.0 {
            r.fps_text = format!("FPS {}", r.frames);
            r.frames = 0;
            r.fps_since_s = frame.time_s;
        }
        let fps = if settings::show_fps(&r.settings) {
            r.fps_text.as_str()
        } else {
            ""
        };
        display::d_display(
            &mut r.screen,
            &mut r.menu,
            &mut r.machines,
            &mut r.game,
            frac,
            fps,
            r.menu_open && settings::menu_dim(&r.settings),
        );
        let (pixels, w, h) = r.screen.backend().frame();
        if w > 0 && h > 0 {
            let (min, max) = fit(&frame, r.display.0 as f32 / r.display.1 as f32);
            picture.shape(Shape::Pixels(Pixels {
                min,
                max,
                width: w,
                height: h,
                rgba: rgba(pixels),
                smooth: false,
            }));
        }
        picture.redraw_in_s = Some(FRAME_S);
        picture.summary = Some(summary(&r.game));
        picture
    }

    fn on_input(&mut self, event: Input) {
        let showing_game = self.running.is_some() && !self.quit;
        let Some(r) = self.running.as_mut().filter(|_| showing_game) else {
            // On the title card: a click, Enter or Space starts the game.
            let go = match &event {
                Input::PointerDown(_) => true,
                Input::KeyDown(k) => matches!(k.name.as_str(), "Enter" | "Space"),
                _ => false,
            };
            if go && self.failed.is_none() {
                self.start = true;
            }
            return;
        };
        match event {
            Input::KeyDown(k) => {
                if let Some(kc) = keys::doom_key(&k.name) {
                    // Cheats see every key in a level, as in the standalone game.
                    if r.game.level.is_some() {
                        r.cheats.check_input(kc, &mut r.game);
                    }
                    if display::input_responder(kc, &mut r.game, &mut r.menu, &mut r.machines) {
                        r.input.events.unset_kb(kc);
                    } else {
                        for k in std::iter::once(kc).chain(keys::alias(kc)) {
                            r.input.events.set_kb(k);
                            r.held.insert(k);
                        }
                    }
                }
            }
            Input::KeyUp(k) => {
                if let Some(kc) = keys::doom_key(&k.name) {
                    for k in std::iter::once(kc).chain(keys::alias(kc)) {
                        r.input.events.unset_kb(k);
                        r.held.remove(&k);
                    }
                }
            }
            Input::MouseMotion(d) => {
                r.mouse.0 += d.x;
                r.mouse.1 += d.y;
            }
            Input::PointerDown(p) => {
                if let Some(b) = mouse_button(p.button) {
                    r.input.events.set_mb(b);
                    r.buttons.insert(b);
                }
            }
            Input::PointerUp(p) => {
                if let Some(b) = mouse_button(p.button) {
                    r.input.events.unset_mb(b);
                    r.buttons.remove(&b);
                }
            }
            // The keyboard (and mouse) gone: every key and button let go.
            Input::Focus(false) => {
                for kc in r.held.drain() {
                    r.input.events.unset_kb(kc);
                }
                for b in r.buttons.drain() {
                    r.input.events.unset_mb(b);
                }
                r.mouse = (0.0, 0.0);
            }
            _ => {}
        }
    }
}

raptaboard_plugin::widget!(Doom);

#[cfg(test)]
mod tests {
    // Tests written by Claude.
    use super::*;

    #[test]
    fn the_display_is_never_narrower_than_four_thirds() {
        assert_eq!(display_size(1265.0, 717.0), (1265, 717));
        assert_eq!(display_size(700.0, 717.0), (956, 717));
        assert_eq!(display_size(300.0, 600.0), (800, 600));
    }

    #[test]
    fn a_narrow_tab_still_gets_doom_s_full_width() {
        for (w, h) in [
            (700.0, 717.0),
            (900.0, 717.0),
            (1265.0, 717.0),
            (333.0, 1001.0),
        ] {
            let (dw, dh) = display_size(w, h);
            let mut screen = RenderStack::<u32>::new(
                settings::resolution(&settings::defaults()),
                render_backend::new_buffer(dw, dh),
                RenderType::Software,
            );
            screen.present(false);
            let (_, fw, fh) = screen.backend().frame();
            assert!(fw >= 320 && fh == 200, "{w}x{h}: {fw}x{fh}");
        }
    }
}
