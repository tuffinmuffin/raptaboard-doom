//! The game's settings: what the Options menu changes, applied as room4doom's
//! window loop applies them (`game-exe/src/loop_winit.rs`, MIT, Copyright (c)
//! 2020 Luke Jones). What only a window, a GPU, a headset or the sound uses
//! (window mode, vsync, health bleed, light gamma, voxels, music) has nothing
//! to change here; the HUD's and free-look's settings the game reads itself.

use gamestate::Game;
use gamestate_traits::{ConfigKey, ConfigTraits as _};
use input::InputState;
use render_backend::{RenderStack, RenderType, ResolutionOptions};

/// Every setting's value, by [`ConfigKey`].
pub type Values = [i32; ConfigKey::KeyCount as usize];

/// The starting settings: room4doom's own defaults (`UserConfig`'s), but low
/// detail (Doom's 320 × 200, scaled up by the dashboard) and the status bar.
pub fn defaults() -> Values {
    let mut a = [0i32; ConfigKey::KeyCount as usize];
    a[ConfigKey::SfxVolume as usize] = 80;
    a[ConfigKey::MusVolume as usize] = 70;
    a[ConfigKey::FrameInterpolation as usize] = 1;
    a[ConfigKey::MenuDim as usize] = 1;
    a[ConfigKey::HudSize as usize] = 1;
    a[ConfigKey::HudMsgMode as usize] = 1;
    a[ConfigKey::HudMsgTime as usize] = 4;
    a[ConfigKey::HealthBleed as usize] = 1;
    a[ConfigKey::MouseSensitivity as usize] = 5;
    a[ConfigKey::LightGamma as usize] = 120;
    a
}

fn on(v: &Values, key: ConfigKey) -> bool {
    v[key as usize] != 0
}

/// The frame's resolution: Detail (High doubles it) and Native UI (the menu
/// and HUD drawn at the tab's own size: sharp text, a larger frame).
pub fn resolution(v: &Values) -> ResolutionOptions {
    ResolutionOptions {
        hi_res: on(v, ConfigKey::HiRes),
        native_ui: on(v, ConfigKey::NativeUi),
        // Software rendering draws the world at its classic size regardless.
        native_world: false,
    }
}

/// The status bar's height in Doom's 200-line space: HUD Size's "bar", or
/// none (the HUD over a full-height view).
pub fn statusbar_height(v: &Values) -> i32 {
    if v[ConfigKey::HudSize as usize] == 1 {
        render_common::STBAR_HEIGHT
    } else {
        0
    }
}

/// Whether to draw between steps (else each step's frame is shown as is).
pub fn interpolate(v: &Values) -> bool {
    on(v, ConfigKey::FrameInterpolation)
}

/// Whether the scene darkens under the menu.
pub fn menu_dim(v: &Values) -> bool {
    on(v, ConfigKey::MenuDim)
}

/// Whether the frame rate shows, top right.
pub fn show_fps(v: &Values) -> bool {
    on(v, ConfigKey::ShowFps)
}

/// Apply every setting to the game's parts: at the start, and whenever the
/// menu changed one.
pub fn apply(v: &Values, game: &mut Game, screen: &mut RenderStack<u32>, input: &mut InputState) {
    game.pic_data.set_crt_gamma(on(v, ConfigKey::CrtGamma));
    input
        .events
        .set_mouse_scale((v[ConfigKey::MouseSensitivity as usize], 1));
    input.events.set_invert_y(on(v, ConfigKey::InvertY));
    reconfigure(v, screen);
    screen.set_statusbar_height(statusbar_height(v));
}

/// The resolution and status bar, every frame as the window loop does: a
/// change made during a screen wipe waits until it ends.
pub fn reconfigure(v: &Values, screen: &mut RenderStack<u32>) {
    if screen.reconfigure(resolution(v), RenderType::Software) {
        screen.set_statusbar_height(statusbar_height(v));
    }
}

/// The game's settings if the menu changed one since the last call.
pub fn changed(game: &mut Game) -> Option<Values> {
    game.is_config_dirty().then(|| {
        game.clear_config_dirty();
        game.config_snapshot()
    })
}

#[cfg(test)]
mod tests {
    // Tests written by Claude.
    use super::*;

    #[test]
    fn the_defaults_dim_the_menu_and_match_the_mouse_the_game_starts_with() {
        let v = defaults();
        assert!(on(&v, ConfigKey::MenuDim));
        // InputState::new's mouse scale, so the menu's slider shows what's used.
        assert_eq!(v[ConfigKey::MouseSensitivity as usize], 5);
        assert!(interpolate(&v));
        assert!(!show_fps(&v));
        assert_eq!(statusbar_height(&v), render_common::STBAR_HEIGHT);
    }

    #[test]
    fn detail_and_native_ui_set_the_resolution() {
        let mut v = defaults();
        let r = resolution(&v);
        assert!(!r.hi_res && !r.native_ui && !r.native_world);
        v[ConfigKey::HiRes as usize] = 1;
        v[ConfigKey::NativeUi as usize] = 1;
        v[ConfigKey::HudSize as usize] = 0;
        let r = resolution(&v);
        assert!(r.hi_res && r.native_ui && !r.native_world);
        assert_eq!(statusbar_height(&v), 0);
        v[ConfigKey::ShowFps as usize] = 1;
        v[ConfigKey::FrameInterpolation as usize] = 0;
        assert!(show_fps(&v) && !interpolate(&v));
    }
}
