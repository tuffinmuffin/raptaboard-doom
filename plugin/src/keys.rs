//! raptaboard's key names (egui's: "A", "1", "Up", "Enter", plus "Ctrl",
//! "Shift", "Alt") as Doom's keys.

use gamestate_traits::KeyCode;

/// The Doom key for a raptaboard key name, if Doom has one.
pub fn doom_key(name: &str) -> Option<KeyCode> {
    use KeyCode as K;
    let letter = |c: char| -> Option<KeyCode> {
        Some(match c {
            'A' => K::A,
            'B' => K::B,
            'C' => K::C,
            'D' => K::D,
            'E' => K::E,
            'F' => K::F,
            'G' => K::G,
            'H' => K::H,
            'I' => K::I,
            'J' => K::J,
            'K' => K::K,
            'L' => K::L,
            'M' => K::M,
            'N' => K::N,
            'O' => K::O,
            'P' => K::P,
            'Q' => K::Q,
            'R' => K::R,
            'S' => K::S,
            'T' => K::T,
            'U' => K::U,
            'V' => K::V,
            'W' => K::W,
            'X' => K::X,
            'Y' => K::Y,
            'Z' => K::Z,
            _ => return None,
        })
    };
    if name.len() == 1
        && let Some(k) = letter(name.chars().next()?)
    {
        return Some(k);
    }
    Some(match name {
        "0" | "Num0" => K::Num0,
        "1" | "Num1" => K::Num1,
        "2" | "Num2" => K::Num2,
        "3" | "Num3" => K::Num3,
        "4" | "Num4" => K::Num4,
        "5" | "Num5" => K::Num5,
        "6" | "Num6" => K::Num6,
        "7" | "Num7" => K::Num7,
        "8" | "Num8" => K::Num8,
        "9" | "Num9" => K::Num9,
        "Enter" => K::Return,
        "Escape" => K::Escape,
        "Backspace" => K::Backspace,
        "Tab" => K::Tab,
        "Space" => K::Space,
        "Minus" => K::Minus,
        "Equals" | "Plus" => K::Equals,
        "Up" | "ArrowUp" => K::Up,
        "Down" | "ArrowDown" => K::Down,
        "Left" | "ArrowLeft" => K::Left,
        "Right" | "ArrowRight" => K::Right,
        "Ctrl" => K::LCtrl,
        "Shift" => K::LShift,
        "Alt" => K::LAlt,
        "F1" => K::F1,
        "F2" => K::F2,
        "F3" => K::F3,
        "F4" => K::F4,
        "F5" => K::F5,
        "F6" => K::F6,
        "F7" => K::F7,
        "F8" => K::F8,
        "F9" => K::F9,
        "F10" => K::F10,
        "F11" => K::F11,
        "F12" => K::F12,
        _ => return None,
    })
}

/// The game controls, room4doom's input config written for raptaboard's
/// keys: the arrows move and turn (W and S too, [`alias`]), A and D strafe,
/// Ctrl fires (raptaboard sends one Ctrl: the left), Space uses, Alt
/// strafes, Shift runs. The mouse buttons stay room4doom's.
pub fn controls() -> input::config::InputConfig {
    use KeyCode as K;
    let ron = format!(
        "(key_right: {}, key_left: {}, key_up: {}, key_down: {}, key_strafeleft: {}, \
         key_straferight: {}, key_fire: {}, key_use: {}, key_strafe: {}, key_speed: {}, \
         mousebfire: {}, mousebstrafe: {}, mousebforward: {})",
        K::Right as i32,
        K::Left as i32,
        K::Up as i32,
        K::Down as i32,
        K::A as i32,
        K::D as i32,
        K::LCtrl as i32,
        K::Space as i32,
        K::LAlt as i32,
        K::LShift as i32,
        gamestate_traits::MouseBtn::Left as u8,
        gamestate_traits::MouseBtn::Middle as u8,
        gamestate_traits::MouseBtn::Right as u8,
    );
    nanoserde::DeRon::deserialize_ron(&ron).expect("the controls are well formed")
}

/// The control key `key` also works as, if any: W is Up, S is Down (one key
/// per control in room4doom's config).
pub fn alias(key: KeyCode) -> Option<KeyCode> {
    match key {
        KeyCode::W => Some(KeyCode::Up),
        KeyCode::S => Some(KeyCode::Down),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    // Tests written by Claude.
    use super::*;

    #[test]
    fn egui_names_reach_doom() {
        // egui's names for the arrows and digits are short: "Up", "1".
        for (name, key) in [
            ("Up", KeyCode::Up),
            ("Left", KeyCode::Left),
            ("1", KeyCode::Num1),
            ("0", KeyCode::Num0),
            ("W", KeyCode::W),
            ("Enter", KeyCode::Return),
            ("Ctrl", KeyCode::LCtrl),
            ("Space", KeyCode::Space),
        ] {
            assert_eq!(doom_key(name), Some(key), "{name}");
        }
        assert_eq!(doom_key("Command"), None);
    }

    #[test]
    fn the_controls_read() {
        // As the game reads them (it panics on a bad key or button).
        let _: input::config::InputConfigResolved = (&controls()).into();
        assert_eq!(alias(KeyCode::W), Some(KeyCode::Up));
        assert_eq!(alias(KeyCode::Q), None);
    }
}
