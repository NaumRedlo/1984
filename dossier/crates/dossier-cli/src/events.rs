//! What a long render is doing, in a form a program can read.
//!
//! Everything this engine says about a render it says to stderr, in sentences
//! meant for a person watching one happen. The bot needs three of those facts
//! while they are still true — how far along the frames are, which clip of a
//! reel they belong to, and the shape of the finished file, which Telegram
//! needs in advance or it draws a square placeholder for a widescreen video.
//!
//! It was getting them by matching regular expressions against that prose. The
//! two halves were each defensible on their own and the seam between them was
//! not: rewording a progress line — a person's sentence, in a file about
//! drawing — silently stopped a live counter in a Telegram chat, and no test
//! on either side could notice, because neither side was wrong.
//!
//! So `--events` opens a second channel. Facts go to stdout as one JSON object
//! per line, the prose stays on stderr exactly as it was, and each is free to
//! change without the other. Nothing is emitted at all unless it is asked for,
//! which keeps a person's terminal a person's terminal.

use std::io::Write;
use std::path::Path;

use crate::report::quote;

/// Whether this run is being watched by a program, and the one place that
/// decides what such a watcher is told.
///
/// Carried rather than read from a global, so that the answer to "does this
/// render report itself" arrives by the same road as every other setting.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Events(bool);

impl Events {
    /// On when the caller asked for it, and silent otherwise.
    pub fn wanted(asked: bool) -> Self {
        Self(asked)
    }

    /// One object, one line, flushed. A watcher reads these while the render
    /// is still running, so a line held in a buffer is a line that arrives too
    /// late to be worth anything.
    fn say(self, line: &str) {
        if !self.0 {
            return;
        }
        let mut out = std::io::stdout().lock();
        let _ = writeln!(out, "{line}");
        let _ = out.flush();
    }

    /// A reel is about to draw its next clip.
    ///
    /// Frames count from zero once per clip, so a counter built from frames
    /// alone reaches a hundred per cent once per clip too — which reads as a
    /// render restarting. This is what tells the two apart.
    pub fn clip(self, index: usize, of: usize, at_ms: f64, reason: &str) {
        self.say(&format!(
            "{{\"event\":\"clip\",\"index\":{index},\"of\":{of},\"at_ms\":{at_ms:.1},\
             \"reason\":{}}}",
            quote(reason)
        ));
    }

    /// How far along the frames are.
    pub fn progress(self, frames: u64, of: u64, per_second: f64, left_seconds: f64) {
        self.say(&format!(
            "{{\"event\":\"progress\",\"frames\":{frames},\"of\":{of},\
             \"per_second\":{per_second:.1},\"left_seconds\":{left_seconds:.1}}}"
        ));
    }

    /// The shape of a finished file, from the process that wrote it.
    ///
    /// A reel says this once per clip and once more for the file it cut them
    /// into; the last one is the one that describes what was actually made.
    pub fn video(self, width: u32, height: u32, seconds: f64) {
        self.say(&format!(
            "{{\"event\":\"video\",\"width\":{width},\"height\":{height},\
             \"seconds\":{seconds:.3}}}"
        ));
    }

    /// The file is on disk and this is where.
    pub fn wrote(self, path: &Path, bytes: u64) {
        self.say(&format!(
            "{{\"event\":\"wrote\",\"path\":{},\"bytes\":{bytes}}}",
            quote(&path.display().to_string())
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn silence_is_the_default_shape_of_this() {
        // Not a formatting test — the point is that a run nobody asked to
        // watch writes nothing at all to stdout, where `exhibit` prints the
        // path of the file it made.
        assert_eq!(Events::wanted(false), Events(false));
    }

    #[test]
    fn a_reason_with_quotes_in_it_stays_one_line_of_json() {
        // Reasons are prose, written elsewhere, and a map called `"osu!"` is
        // not a reason to hand a watcher a broken stream.
        let reason = "a \"1425x\" run\nbreaks";
        let line = format!("{{\"reason\":{}}}", quote(reason));
        assert_eq!(line, r#"{"reason":"a \"1425x\" run\nbreaks"}"#);
        assert_eq!(line.lines().count(), 1);
    }
}
