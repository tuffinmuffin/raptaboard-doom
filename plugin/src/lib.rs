//! The FRC 2026 field in Doom as a raptaboard tab plugin: room4doom's game and
//! software renderer, run by raptaboard instead of a window.
//!
//! The game steps 35 times a second by the frame's clock and is drawn about
//! 60 times a second between steps (room4doom interpolates), as a pixel
//! frame. Keys come while the tab has the keyboard. The WADs are read from
//! the plugin's own folder (`/pack`). Cheats work as in the standalone game.
//! Sound isn't mixed yet: the game's sound messages are dropped.

mod cheats;
mod display;
mod keys;

use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver};

use doom_ui::{Finale, GameMenu, Intermission, Messages, Statusbar};
use game_config::GameOptions;
use gamestate::Game;
use gamestate::subsystems::GameSubsystem;
use gamestate_traits::{ConfigKey, KeyCode, SubsystemTrait};
use input::InputState;
use raptaboard_plugin::prelude::*;
use render_backend::{RenderStack, RenderType, ResolutionOptions};
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
    /// The frame's time the game started at.
    started_s: f64,
    /// Doom keys held, so all can be let go when the tab loses the keyboard.
    held: HashSet<KeyCode>,
    /// The cheat codes typed so far (`iddqd`).
    cheats: cheats::Cheats,
}

#[derive(Default)]
struct Doom {
    running: Option<Running>,
    /// Why the game couldn't start.
    failed: Option<String>,
}

/// The starting settings room4doom's own config gives, less what only a
/// window or headset uses.
fn config() -> [i32; ConfigKey::KeyCount as usize] {
    let mut a = [0i32; ConfigKey::KeyCount as usize];
    a[ConfigKey::SfxVolume as usize] = 10;
    a[ConfigKey::MusVolume as usize] = 6;
    a[ConfigKey::FrameInterpolation as usize] = 1;
    a[ConfigKey::HudSize as usize] = 1;
    a[ConfigKey::HudMsgMode as usize] = 1;
    a[ConfigKey::HudMsgTime as usize] = 4;
    a[ConfigKey::MouseSensitivity as usize] = 10;
    a[ConfigKey::FreeLookShoot as usize] = 0;
    a
}

fn start(time_s: f64, width: u32, height: u32) -> Result<Running, String> {
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
    let game = Game::new(options, wad, sound_tx, None, config());
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
        resolution(),
        render_backend::new_buffer(width, height),
        RenderType::Software,
    );
    screen.set_statusbar_height(render_common::STBAR_HEIGHT);
    let input = InputState::new((&keys::controls()).into());
    Ok(Running {
        game,
        input,
        menu,
        machines,
        screen,
        sound,
        tics: 0,
        started_s: time_s,
        held: HashSet::new(),
        cheats: cheats::Cheats::new(),
    })
}

/// Doom's classic 320 × 200, scaled up by the dashboard.
fn resolution() -> ResolutionOptions {
    ResolutionOptions {
        hi_res: false,
        native_ui: false,
        native_world: false,
    }
}

/// An ARGB frame as RGBA bytes.
fn rgba(argb: &[u32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(argb.len() * 4);
    for p in argb {
        out.extend([(p >> 16) as u8, (p >> 8) as u8, *p as u8, 255]);
    }
    out
}

/// The rect `w` × `h` pixels take, as large as fits in `frame` and centered:
/// Doom's 4:3 (its 320 × 200 has tall pixels).
fn fit(frame: &FrameInfo, w: u32, h: u32) -> (Point, Point) {
    let aspect = if (w, h) == (320, 200) {
        4.0 / 3.0
    } else {
        w as f32 / h.max(1) as f32
    };
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

/// What a screen reader says for the game (and tests read): the player's
/// health and armor, and god mode when it's on.
fn summary(game: &Game) -> String {
    let player = &game.players[game.consoleplayer];
    let status = &player.status;
    // In a level the health is its map object's; between levels, the status's.
    let health = player.mobj().map_or(status.health, |m| m.health);
    let god = status.cheats.contains(gameplay::PlayerCheat::Godmode);
    format!(
        "Doom: health {}, armor {}{}",
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
        if self.running.is_none() && self.failed.is_none() {
            let (w, h) = (frame.size.width as u32, frame.size.height as u32);
            match start(frame.time_s, w, h) {
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
            display::run_game_tic(&mut r.game, &mut r.input, &mut r.menu, &mut r.machines);
            r.tics += 1;
        }
        // Not mixed yet.
        while r.sound.try_recv().is_ok() {}
        let frac = (clock - clock.floor()) as f32;
        display::d_display(
            &mut r.screen,
            &mut r.menu,
            &mut r.machines,
            &mut r.game,
            frac,
        );
        let (pixels, w, h) = r.screen.backend().frame();
        if w > 0 && h > 0 {
            let (min, max) = fit(&frame, w, h);
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
        let Some(r) = &mut self.running else {
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
            // The keyboard gone: every key let go.
            Input::Focus(false) => {
                for kc in r.held.drain() {
                    r.input.events.unset_kb(kc);
                }
            }
            _ => {}
        }
    }
}

raptaboard_plugin::widget!(Doom);
