//! Hit sounds, synthesised.
//!
//! osu!'s own samples ship with the game and aren't ours to redistribute, so
//! these are made from scratch: a little noise, a little sine, an envelope.
//! That turns out to be an advantage rather than a compromise — the sounds are
//! a few lines of arithmetic instead of a licensing question, they need no
//! files on disk, and they can be tuned without asking anyone.
//!
//! What this is not: a sample-set implementation. osu! picks between normal,
//! soft and drum sets per timing point and per object, and maps can carry their
//! own audio. Here a note's sound comes from its hitsound bits alone. That gets
//! the rhythm and the accents right, which is what a replay video needs; the
//! exact timbre of someone's custom skin does not survive into a video anyway.

mod synth;
mod track;

pub use synth::Voice;
pub use track::Track;

/// Everything is generated and mixed at this rate, and handed to the encoder
/// as raw PCM at it.
pub const SAMPLE_RATE: u32 = 44_100;
