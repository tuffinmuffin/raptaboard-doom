//! One game step and one frame's drawing, as room4doom's `game-exe/src/d_main.rs`
//! does them (MIT, Copyright (c) 2020 Luke Jones), kept to what an embedded
//! software renderer needs: no VR, no GPU effects, no FPS text.

use std::ptr;

use doom_ui::{Finale, Intermission, Messages, Statusbar};
use gameplay::{MapObjFlag, Player};
use gamestate::Game;
use gamestate::subsystems::GameSubsystem;
use gamestate_traits::{ConfigKey, GameState, KeyCode, SubsystemTrait};
use input::InputState;
use math::{Angle, Bam, FixedT, lookdir_to_radian};
use render_backend::RenderStack;
use render_common::{ByteOrder, DrawBuffer, HeadFrame, PixelFmt, RenderPspDef, RenderView};
use ui_draw::UiCanvas;
use wad::types::{BLACK, WadPatch};

/// The UI subsystems drawn over the world.
pub type Machines = GameSubsystem<Intermission, Statusbar, Messages, Finale>;

/// The view from the console player, between the last two steps by `frac`.
fn build_render_view(player: &Player, frac: f32, game_tic: u32) -> Option<RenderView> {
    let mobj = player.mobj()?;
    let prev = &player.prev_render;
    let frac_fp = FixedT::from_f32(frac);
    let lerp_fp = |a: FixedT, b: FixedT| -> FixedT { a + (b - a) * frac_fp };
    let curr_bam = mobj.angle.to_bam();
    let angle_delta = curr_bam.wrapping_sub(prev.angle_bam) as i32;
    let interp_bam = prev
        .angle_bam
        .wrapping_add((angle_delta as f32 * frac) as i32 as u32);
    let lookdir_delta = (player.lookdir - prev.lookdir) as f32;
    let interp_lookdir = prev.lookdir as f32 + lookdir_delta * frac;
    let psprites = std::array::from_fn(|i| {
        let psp = &player.psprites[i];
        match psp.state {
            Some(s) => RenderPspDef {
                active: true,
                sprite: s.sprite as usize,
                frame: s.frame,
                sx: prev.psp_sx[i] + (psp.sx - prev.psp_sx[i]) * frac,
                sy: prev.psp_sy[i] + (psp.sy - prev.psp_sy[i]) * frac,
            },
            None => RenderPspDef::default(),
        }
    });
    Some(RenderView {
        x: lerp_fp(prev.x, mobj.x),
        y: lerp_fp(prev.y, mobj.y),
        z: lerp_fp(prev.z, mobj.z),
        viewz: lerp_fp(prev.viewz, player.viewz),
        viewheight: player.viewheight,
        angle: Angle::<Bam>::from_bam(interp_bam),
        lookdir: lookdir_to_radian(interp_lookdir),
        fixedcolormap: player.fixedcolormap as usize,
        extralight: player.extralight,
        is_shadow: mobj.flags.contains(MapObjFlag::Shadow),
        psprites,
        sector_lightlevel: mobj.subsector.sector.lightlevel,
        player_mobj_id: ptr::from_ref(mobj) as usize,
        frac,
        frac_fp,
        game_tic,
        head_rotation: None,
        weapon_anchor: None,
        teleport: None,
    })
}

/// A key pressed: the menu and on-screen subsystems first. Returns whether
/// one of them took it (else it's a game control).
pub fn input_responder(
    sc: KeyCode,
    game: &mut Game,
    menu: &mut impl SubsystemTrait,
    machines: &mut Machines,
) -> bool {
    if menu.responder(sc, game) {
        return true;
    }
    if machines.hud_msgs.responder(sc, game) {
        return true;
    }
    if game.level.is_none() {
        match game.gamestate {
            GameState::Intermission => return machines.intermission.responder(sc, game),
            GameState::Finale => return machines.finale.responder(sc, game),
            _ => {}
        }
    }
    false
}

/// One step of the game (a 35th of a second). Returns whether the menu shows.
pub fn run_game_tic(
    game: &mut Game,
    input: &mut InputState,
    menu: &mut impl SubsystemTrait,
    machines: &mut Machines,
) -> bool {
    if game.demo.advance {
        game.do_advance_demo();
    }
    set_look_aim(game);
    let menu_active = menu.ticker(game);
    let in_user_game = game.gamestate == GameState::Level && !game.demo.playback;
    let menu_blocks = menu_active && in_user_game;
    if !menu_blocks && game.wipe_game_state != GameState::ForceWipe {
        game.ticker(machines);
        game.frozen = false;
    } else {
        machines.statusbar.ticker(game);
        machines.hud_msgs.ticker(game);
        game.frozen = true;
    }
    game.game_tic += 1;
    let console_player = game.consoleplayer;
    if game.gamestate == game.wipe_game_state {
        game.netcmds[console_player][0] = input.events.build_tic_cmd(&input.config);
    }
    menu_active
}

fn set_look_aim(game: &mut Game) {
    let player = &game.players[game.consoleplayer];
    if player.aim.is_some() || game.config_values[ConfigKey::FreeLookShoot as usize] == 0 {
        return;
    }
    let ray = player.look_ray();
    game.players[game.consoleplayer].aim = ray;
}

fn page_drawer(game: &Game, draw_buf: &mut impl DrawBuffer) {
    let black = PixelFmt::from_argb(BLACK, ByteOrder::Argb);
    draw_buf.buf_mut().fill(black);
    let viewport = draw_buf.ui_layout().centered();
    let palette = game.pic_data.wad_palette();
    UiCanvas::new(draw_buf, palette, viewport).patch(&game.page.cache, [0.0, 0.0]);
}

fn refresh_title_page(game: &mut Game) {
    if game.page.cache.name != game.page.name
        && let Some(lump) = game.wad_data.get_lump(game.page.name)
    {
        game.page.cache = WadPatch::from_lump(lump);
    }
}

/// Draw one frame, `frac` of the way from the last step to the next, the
/// scene dimmed under the menu when `menu_open` (Doom's red menu text is hard
/// to read over bright walls, scaled up in a window).
pub fn d_display(
    screen: &mut RenderStack<u32>,
    menu: &mut impl SubsystemTrait,
    machines: &mut Machines,
    game: &mut Game,
    frac: f32,
    menu_open: bool,
) {
    let wipe = game.gamestate != game.wipe_game_state;
    if wipe && !screen.is_wiping() {
        screen.start_wipe();
    }
    let frac = if screen.is_wiping() || game.frozen {
        1.0
    } else {
        frac
    };
    let head = HeadFrame::default();
    screen.set_eye(0);
    if game.gamestate == GameState::Level
        && let Some(level) = game.level.as_mut()
        && game.players_in_game[game.consoleplayer]
    {
        level.level_data.apply_render_interpolation(frac);
        let player = &game.players[game.consoleplayer];
        if let Some(view) = build_render_view(player, frac, game.game_tic) {
            let _ = &head;
            game.pic_data.set_player_palette(
                player.status.damagecount,
                player.status.bonuscount,
                player.status.powers[gameplay::PowerType::Strength as usize],
                player.status.powers[gameplay::PowerType::IronFeet as usize],
            );
            screen.render_player_view(&view, &level.level_data, &mut game.pic_data);
        }
        level.level_data.restore_render_interpolation();
    }
    match game.gamestate {
        GameState::Level => {
            machines.statusbar.draw(screen.hud_frame());
            machines.hud_msgs.draw(screen.hud_frame());
        }
        GameState::Intermission => machines.intermission.draw(screen.screen_frame()),
        GameState::Finale => machines.finale.draw(screen.screen_frame()),
        GameState::DemoScreen => {
            refresh_title_page(game);
            page_drawer(game, screen.screen_frame());
        }
        GameState::ForceWipe => {}
    }
    if menu_open {
        dim(screen.screen_frame().buf_mut());
    }
    menu.draw(screen.screen_frame());
    screen.upload_ui();
    screen.present(wipe);
    if wipe && screen.do_wipe() {
        game.wipe_game_state = game.gamestate;
        game.players[game.consoleplayer].save_prev_render();
    }
}

/// Darken ARGB pixels to 3/8 of their brightness.
fn dim(pixels: &mut [u32]) {
    for p in pixels {
        let half = (*p >> 1) & 0x007F_7F7F;
        let eighth = (*p >> 3) & 0x001F_1F1F;
        *p = 0xFF00_0000 | (half - eighth);
    }
}

#[cfg(test)]
mod tests {
    // Tests written by Claude.
    use super::*;

    #[test]
    fn dimming_darkens_each_channel_and_keeps_alpha() {
        let mut px = [0xFFFF_8040, 0xFF00_0000, 0xFF10_2030];
        dim(&mut px);
        assert_eq!(px, [0xFF60_3018, 0xFF00_0000, 0xFF06_0C12]);
    }
}
