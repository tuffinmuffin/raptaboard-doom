//! The game's sound, mixed here and sent to raptaboard's speakers
//! (`play-audio`) a frame's worth at a time: room4doom's rodio sound server
//! (`sound/rodio`, MIT, Copyright (c) 2020 Luke Jones) without its audio
//! device or thread. Effects are the WAD's `DS*` lumps on 32 channels, panned
//! and quieter with distance from the player; music is the WAD's tracks on
//! room4doom's OPL2 emulator.

use std::sync::Arc;

use opl2_emulator::OplPlayerState;
use sound_common::{
    MAX_DIST, MIXER_CHANNELS, SAMPLE_RATE, SFX_INFO_BASE, SfxName, SoundAction, dist_from_points,
    listener_to_source_angle_deg,
};
use wad::WadData;

/// Samples a second, each stereo.
pub const RATE: u32 = SAMPLE_RATE;
/// How far ahead of the clock sound is sent, in seconds: enough that a slow
/// frame doesn't leave a gap, little enough that a shot is heard as fired.
const LEAD_S: f64 = 0.08;

/// One effect playing.
#[derive(Clone, Default)]
struct Channel {
    /// Mono samples at [`RATE`], shared with the loaded effect.
    samples: Arc<[f32]>,
    cursor: usize,
    active: bool,
    priority: i32,
    /// -1 full left, 1 full right.
    pan: f32,
    /// 1 beside the player, 0 at [`MAX_DIST`].
    distance_vol: f32,
    /// What made it (0: no place, as the menu's), and where.
    uid: usize,
    x: f32,
    y: f32,
}

/// The player's ears.
#[derive(Clone, Copy, Default)]
struct Listener {
    uid: usize,
    x: f32,
    y: f32,
    angle: f32,
}

pub struct Audio {
    /// Each effect by [`SfxName`]: its samples and priority.
    effects: Vec<(Arc<[f32]>, i32)>,
    channels: Vec<Channel>,
    listener: Listener,
    /// The effects' volume, 0 to 1 (the game's 0 to 128).
    sfx_volume: f32,
    music: OplPlayerState,
    /// Stereo frames sent so far, and the clock's time they began at.
    sent: u64,
    started_s: Option<f64>,
}

/// A WAD sound lump (8-bit unsigned, its own rate) as mono samples at
/// [`RATE`], by linear interpolation. `None` if it isn't one.
fn lump_samples(lump: &[u8]) -> Option<Vec<f32>> {
    let rate = f64::from(i16::from_le_bytes([*lump.get(2)?, *lump.get(3)?]));
    let len = u32::from_le_bytes(lump.get(4..8)?.try_into().ok()?) as usize;
    let pcm = lump.get(8..len.min(lump.len()))?;
    if rate <= 0.0 || pcm.is_empty() {
        return None;
    }
    let at = |i: usize| (f32::from(pcm[i.min(pcm.len() - 1)]) - 128.0) / 128.0;
    let ratio = f64::from(RATE) / rate;
    let n = (pcm.len() as f64 * ratio) as usize;
    Some(
        (0..n)
            .map(|i| {
                let pos = i as f64 / ratio;
                let (idx, frac) = (pos as usize, pos.fract() as f32);
                at(idx) + (at(idx + 1) - at(idx)) * frac
            })
            .collect(),
    )
}

/// How loud a sound `dist` away is: full beside, silent at [`MAX_DIST`].
fn dist_scale(dist: f32) -> f32 {
    (1.0 - dist / MAX_DIST).clamp(0.0, 1.0)
}

/// Pan for a sound `angle_deg` round from where the player faces.
fn angle_to_pan(angle_deg: f32) -> f32 {
    angle_deg.to_radians().sin()
}

impl Audio {
    /// The effects and music instruments from `wad`, at the game's volumes
    /// (0 to 128).
    pub fn new(wad: &WadData, sfx_volume: i32, music_volume: i32) -> Self {
        let effects = SFX_INFO_BASE
            .iter()
            .map(|s| {
                let name = format!("DS{}", s.name.to_ascii_uppercase());
                let samples = wad
                    .get_lump(&name)
                    .and_then(|l| lump_samples(&l.data))
                    .unwrap_or_default();
                (Arc::from(samples), s.priority)
            })
            .collect();
        let mut audio = Self {
            effects,
            channels: vec![Channel::default(); MIXER_CHANNELS as usize],
            listener: Listener::default(),
            sfx_volume: 0.0,
            music: OplPlayerState::new(RATE, wad),
            sent: 0,
            started_s: None,
        };
        audio.handle(SoundAction::SfxVolume(sfx_volume));
        audio.handle(SoundAction::MusicVolume(music_volume));
        audio
    }

    /// One of the game's sound messages.
    pub fn handle(&mut self, action: SoundAction) {
        match action {
            SoundAction::StartSfx { uid, sfx, x, y } => self.start(uid, sfx, x, y),
            SoundAction::UpdateListener { uid, x, y, angle } => {
                self.listener = Listener { uid, x, y, angle };
                self.place_all();
            }
            SoundAction::StopSfx { uid } => self.stop(uid),
            SoundAction::StopSfxAll | SoundAction::Shutdown => {
                self.channels.iter_mut().for_each(|c| c.active = false);
                self.music.stop_playback();
            }
            SoundAction::SfxVolume(v) => self.sfx_volume = v as f32 / 128.0,
            SoundAction::MusicVolume(v) => {
                self.music.volume = v.clamp(0, 128);
                self.music.refresh_all_volumes();
            }
            SoundAction::StartMusic(data, looping) | SoundAction::ChangeMusic(data, looping) => {
                self.music.stop_playback();
                if !data.is_empty() && self.music.load_music(&data).is_ok() {
                    self.music.start_playback(looping);
                }
            }
            SoundAction::PauseMusic | SoundAction::StopMusic => self.music.stop_playback(),
            SoundAction::ResumeMusic => self.music.start_playback(true),
            // OPL2 only: there's no SoundFont for GUS music here.
            SoundAction::SetMusicType(_) => {}
        }
    }

    /// Pan and distance for a sound at `x`, `y` from `uid`.
    fn placed(&self, uid: usize, x: f32, y: f32) -> (f32, f32) {
        let l = self.listener;
        let dist = dist_from_points(l.x, l.y, x, y);
        let pan = if uid != 0 && uid != l.uid {
            angle_to_pan(listener_to_source_angle_deg(l.x, l.y, l.angle, x, y))
        } else {
            0.0
        };
        (pan, dist)
    }

    fn start(&mut self, uid: usize, sfx: SfxName, x: f32, y: f32) {
        // No place (the menu's, the player's own): where the player is.
        let (x, y) = if uid == 0 {
            (self.listener.x, self.listener.y)
        } else {
            (x, y)
        };
        let (pan, dist) = self.placed(uid, x, y);
        if dist >= MAX_DIST {
            return;
        }
        self.stop(uid);
        let Some((samples, priority)) = self.effects.get(sfx as usize).cloned() else {
            return;
        };
        if samples.is_empty() {
            return;
        }
        // A free channel, else the first no more important.
        let free = self.channels.iter().position(|c| !c.active);
        let Some(c) = free.or_else(|| self.channels.iter().position(|c| priority >= c.priority))
        else {
            return;
        };
        self.channels[c] = Channel {
            samples,
            cursor: 0,
            active: true,
            priority,
            pan,
            distance_vol: dist_scale(dist),
            uid,
            x,
            y,
        };
    }

    fn stop(&mut self, uid: usize) {
        for c in self
            .channels
            .iter_mut()
            .filter(|c| c.active && c.uid == uid)
        {
            c.active = false;
        }
    }

    /// The player moved or turned: every placed sound's pan and volume again.
    fn place_all(&mut self) {
        for i in 0..self.channels.len() {
            let c = &self.channels[i];
            if !c.active || c.uid == 0 {
                continue;
            }
            let (pan, dist) = self.placed(c.uid, c.x, c.y);
            let c = &mut self.channels[i];
            if dist >= MAX_DIST {
                c.active = false;
                continue;
            }
            c.distance_vol = dist_scale(dist);
            if c.uid != self.listener.uid {
                c.pan = pan;
            }
        }
    }

    /// `frames` stereo frames of effects and music, interleaved.
    pub fn mix(&mut self, frames: usize) -> Vec<i16> {
        let mut music = vec![0i16; frames];
        self.music.generate_samples(&mut music);
        let mut out: Vec<f32> = music
            .iter()
            .flat_map(|&m| {
                let m = f32::from(m) / 32768.0;
                [m, m]
            })
            .collect();
        for c in self.channels.iter_mut().filter(|c| c.active) {
            // Equal-gain pan, as the standalone's mixer.
            let right = (c.pan + 1.0) * 0.5 * c.distance_vol * self.sfx_volume;
            let left = (1.0 - (c.pan + 1.0) * 0.5) * c.distance_vol * self.sfx_volume;
            let n = frames.min(c.samples.len() - c.cursor);
            for (f, s) in c.samples[c.cursor..c.cursor + n].iter().enumerate() {
                out[f * 2] += s * left;
                out[f * 2 + 1] += s * right;
            }
            c.cursor += n;
            c.active = c.cursor < c.samples.len();
        }
        out.iter()
            .map(|s| (s.clamp(-1.0, 1.0) * 32767.0) as i16)
            .collect()
    }

    /// How many frames to send at `time_s` (the frame's clock) to stay
    /// [`LEAD_S`] ahead of it. After a gap (the tab closed), only the lead:
    /// what would have played then isn't caught up on.
    pub fn due(&mut self, time_s: f64) -> usize {
        let started = *self.started_s.get_or_insert(time_s);
        let lead = (LEAD_S * f64::from(RATE)) as u64;
        let now = ((time_s - started).max(0.0) * f64::from(RATE)) as u64;
        if self.sent < now {
            self.sent = now;
        }
        let due = (now + lead).saturating_sub(self.sent);
        self.sent += due;
        due as usize
    }
}

#[cfg(test)]
mod tests {
    // Tests written by Claude.
    use super::*;

    /// A WAD with nothing in it: no effects, no instruments.
    fn empty_wad() -> WadData {
        let path = std::env::temp_dir().join(format!("doom-audio-{}.wad", std::process::id()));
        let mut wad = b"PWAD".to_vec();
        wad.extend(0u32.to_le_bytes());
        wad.extend(12u32.to_le_bytes());
        std::fs::write(&path, wad).unwrap();
        WadData::new(&path)
    }

    /// An 8-bit lump: `rate` a second, these samples.
    fn lump(rate: u16, pcm: &[u8]) -> Vec<u8> {
        let mut l = vec![3, 0];
        l.extend(rate.to_le_bytes());
        l.extend((pcm.len() as u32 + 8).to_le_bytes());
        l.extend(pcm);
        l
    }

    #[test]
    fn a_lump_is_read_at_the_mixer_s_rate() {
        let s = lump_samples(&lump(11_025, &[128, 255, 0, 128])).unwrap();
        assert_eq!(s.len(), 16, "four at 11 025 is sixteen at 44 100");
        assert_eq!(s[0], 0.0);
        assert!((s[4] - 127.0 / 128.0).abs() < 1e-6);
        assert_eq!(s[8], -1.0);
        assert!(lump_samples(&[3, 0, 0, 0]).is_none());
        assert!(lump_samples(&lump(0, &[1])).is_none());
    }

    #[test]
    fn sound_is_sent_a_little_ahead_of_the_clock_and_not_caught_up() {
        let mut a = Audio::new(&empty_wad(), 128, 128);
        let lead = (LEAD_S * f64::from(RATE)) as usize;
        assert_eq!(a.due(10.0), lead, "the lead, first");
        assert_eq!(a.due(10.0), 0);
        // A frame later: that frame's worth.
        assert_eq!(a.due(10.05), RATE as usize / 20);
        // Ten seconds unseen: just the lead again.
        assert_eq!(a.due(20.5), lead);
    }

    #[test]
    fn effects_are_panned_and_quieter_far_away() {
        let mut a = Audio::new(&empty_wad(), 128, 128);
        // Only the pistol (SfxName 1), half loud, eight samples.
        a.effects = vec![(Arc::from([]), 0), (Arc::from(vec![0.5f32; 8]), 64)];
        a.handle(SoundAction::UpdateListener {
            uid: 1,
            x: 0.0,
            y: 0.0,
            angle: 0.0,
        });
        // Beside the player, from the player: centered.
        a.handle(SoundAction::StartSfx {
            uid: 1,
            sfx: SfxName::Pistol,
            x: 0.0,
            y: 0.0,
        });
        let out = a.mix(10);
        let near = i32::from(out[0]) + i32::from(out[1]);
        assert_eq!(out[0], out[1]);
        assert!(near > 16000, "{near}");
        assert_eq!(out[16], 0, "eight samples, then silence");
        assert!(!a.channels[0].active);
        // Halfway to silence, from something else, off to one side.
        a.handle(SoundAction::StartSfx {
            uid: 2,
            sfx: SfxName::Pistol,
            x: 0.0,
            y: MAX_DIST / 2.0,
        });
        let out = a.mix(1);
        assert_ne!(out[0], out[1], "panned");
        let far = i32::from(out[0]) + i32::from(out[1]);
        assert!((far - near / 2).abs() <= 1, "{far} vs {near}");
        // Out of earshot: nothing.
        a.handle(SoundAction::StartSfx {
            uid: 3,
            sfx: SfxName::Pistol,
            x: MAX_DIST,
            y: 0.0,
        });
        assert_eq!(a.channels.iter().filter(|c| c.active).count(), 1);
    }
}
