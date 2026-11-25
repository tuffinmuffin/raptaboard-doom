//! raptaboard's key names (egui's, plus "Ctrl", "Shift", "Alt") as Doom's keys.

use gamestate_traits::KeyCode;

/// The Doom key for a raptaboard key name, if Doom has one.
pub fn doom_key(name: &str) -> Option<KeyCode> {
    use KeyCode as K;
    let letter = |c: char| -> Option<KeyCode> {
        Some(match c {
            'A' => K::A, 'B' => K::B, 'C' => K::C, 'D' => K::D, 'E' => K::E, 'F' => K::F,
            'G' => K::G, 'H' => K::H, 'I' => K::I, 'J' => K::J, 'K' => K::K, 'L' => K::L,
            'M' => K::M, 'N' => K::N, 'O' => K::O, 'P' => K::P, 'Q' => K::Q, 'R' => K::R,
            'S' => K::S, 'T' => K::T, 'U' => K::U, 'V' => K::V, 'W' => K::W, 'X' => K::X,
            'Y' => K::Y, 'Z' => K::Z,
            _ => return None,
        })
    };
    if name.len() == 1 {
        return letter(name.chars().next()?);
    }
    Some(match name {
        "Num0" => K::Num0, "Num1" => K::Num1, "Num2" => K::Num2, "Num3" => K::Num3,
        "Num4" => K::Num4, "Num5" => K::Num5, "Num6" => K::Num6, "Num7" => K::Num7,
        "Num8" => K::Num8, "Num9" => K::Num9,
        "Enter" => K::Return,
        "Escape" => K::Escape,
        "Backspace" => K::Backspace,
        "Tab" => K::Tab,
        "Space" => K::Space,
        "Minus" => K::Minus,
        "Equals" | "Plus" => K::Equals,
        "ArrowUp" => K::Up,
        "ArrowDown" => K::Down,
        "ArrowLeft" => K::Left,
        "ArrowRight" => K::Right,
        "Ctrl" => K::LCtrl,
        "Shift" => K::LShift,
        "Alt" => K::LAlt,
        "F1" => K::F1, "F2" => K::F2, "F3" => K::F3, "F4" => K::F4, "F5" => K::F5,
        "F6" => K::F6, "F7" => K::F7, "F8" => K::F8, "F9" => K::F9, "F10" => K::F10,
        "F11" => K::F11, "F12" => K::F12,
        _ => return None,
    })
}
