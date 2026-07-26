//! Turning one judged replay into output — for a person or for a program.

use dossier_sim::{MissContext, Verification};

use dossier_replay::HitCounts;

/// What the `.osr` header says, before any map is involved.
pub struct Header {
    pub replay_path: String,
    pub player: String,
    pub mode: String,
    pub mods: String,
    pub beatmap_hash: String,
    pub counts: HitCounts,
    pub max_combo: u32,
    pub frames: usize,
    pub duration_ms: i64,
}

impl Header {
    pub fn human(&self) -> String {
        format!(
            "── {}\n   player  {}   mode {}   mods {}\n   map     {}\n   score   {}/{}/{}/{}  {}x  {:.2}%\n   frames  {} over {:.1}s\n",
            self.replay_path,
            self.player,
            self.mode,
            self.mods,
            self.beatmap_hash,
            self.counts.count_300,
            self.counts.count_100,
            self.counts.count_50,
            self.counts.count_miss,
            self.max_combo,
            self.counts.accuracy_std(),
            self.frames,
            self.duration_ms as f64 / 1000.0,
        )
    }

    pub fn json(&self) -> String {
        format!(
            concat!(
                "{{\"replay\":{},\"player\":{},\"mode\":{},\"mods\":{},\"beatmap_hash\":{},",
                "\"counts\":{},\"max_combo\":{},\"accuracy\":{:.4},\"frames\":{},\"duration_ms\":{}}}"
            ),
            quote(&self.replay_path),
            quote(&self.player),
            quote(&self.mode),
            quote(&self.mods),
            quote(&self.beatmap_hash),
            counts_json(self.counts),
            self.max_combo,
            self.counts.accuracy_std(),
            self.frames,
            self.duration_ms,
        )
    }
}

pub struct Report {
    pub replay_path: String,
    pub map_source: String,
    pub title: String,
    pub player: String,
    pub mods: String,
    pub objects: usize,
    pub check: Verification,
    pub our_accuracy: f64,
    pub their_accuracy: f64,
    pub misses: Vec<MissContext>,
}

/// What our misses have in common — the difference between "the simulator put
/// the note in the wrong place" and "the player missed".
pub struct MissSummary {
    pub circles: usize,
    pub sliders: usize,
    pub spinners: usize,
    /// Misses with a click close by in time.
    pub with_nearby_click: usize,
    /// …of those, the ones that landed just outside the circle.
    pub geometry_suspects: usize,
    /// Median overshoot of those, in osu!pixels past the edge.
    pub median_overshoot_px: Option<f64>,
}

impl MissSummary {
    pub fn of(misses: &[MissContext]) -> Self {
        let mut overshoots: Vec<f64> = misses
            .iter()
            .filter(|m| m.looks_like_a_geometry_error())
            .filter_map(|m| m.press_distance_px.map(|d| d - m.radius_px))
            .collect();
        overshoots.sort_by(f64::total_cmp);

        Self {
            circles: misses.iter().filter(|m| m.kind == "circle").count(),
            sliders: misses.iter().filter(|m| m.kind == "slider").count(),
            spinners: misses.iter().filter(|m| m.kind == "spinner").count(),
            with_nearby_click: misses.iter().filter(|m| m.press_dt_ms.is_some()).count(),
            geometry_suspects: overshoots.len(),
            median_overshoot_px: overshoots.get(overshoots.len() / 2).copied(),
        }
    }

    fn json(&self) -> String {
        let median = match self.median_overshoot_px {
            Some(value) => format!("{value:.2}"),
            None => "null".to_owned(),
        };
        format!(
            concat!(
                "{{\"circle\":{},\"slider\":{},\"spinner\":{},\"with_nearby_click\":{},",
                "\"geometry_suspects\":{},\"median_overshoot_px\":{}}}"
            ),
            self.circles,
            self.sliders,
            self.spinners,
            self.with_nearby_click,
            self.geometry_suspects,
            median,
        )
    }
}

impl Report {
    pub fn is_exact(&self) -> bool {
        self.check.is_exact()
    }

    pub fn human(&self) -> String {
        let mut out = String::new();
        out.push_str(&format!("── {}\n", self.replay_path));
        out.push_str(&format!("   map     {}\n", self.title));
        out.push_str(&format!("   file    {}\n", self.map_source));
        out.push_str(&format!(
            "   player  {}   mods {}   objects {}\n\n",
            self.player, self.mods, self.objects
        ));

        let ours = self.check.ours;
        let theirs = self.check.theirs;
        out.push_str("             ours    replay\n");
        for (label, a, b) in [
            (
                "300",
                u32::from(ours.count_300),
                u32::from(theirs.count_300),
            ),
            (
                "100",
                u32::from(ours.count_100),
                u32::from(theirs.count_100),
            ),
            ("50", u32::from(ours.count_50), u32::from(theirs.count_50)),
            (
                "miss",
                u32::from(ours.count_miss),
                u32::from(theirs.count_miss),
            ),
            (
                "combo",
                self.check.our_max_combo,
                self.check.their_max_combo,
            ),
        ] {
            let mark = if a == b { ' ' } else { '!' };
            out.push_str(&format!("   {label:>6} {a:>8} {b:>9}  {mark}\n"));
        }
        let acc_mark = if (self.our_accuracy - self.their_accuracy).abs() < 0.005 {
            ' '
        } else {
            '!'
        };
        out.push_str(&format!(
            "   {:>6} {:>7.2}% {:>8.2}%  {acc_mark}\n\n",
            "acc", self.our_accuracy, self.their_accuracy
        ));

        out.push_str(&format!("   {}\n", self.verdict()));
        out
    }

    /// Per-miss detail, for when the totals disagree and the question is why.
    pub fn explain(&self) -> String {
        if self.misses.is_empty() {
            return "   no misses to explain\n".to_owned();
        }
        let mut out = String::from("   our misses:\n");
        for miss in &self.misses {
            let where_ = match (miss.press_dt_ms, miss.press_distance_px) {
                (Some(dt), Some(distance)) => format!(
                    "click {dt:+.0}ms, {distance:.1}px from centre (radius {:.1}){}",
                    miss.radius_px,
                    if miss.looks_like_a_geometry_error() {
                        "  ← just outside"
                    } else {
                        ""
                    }
                ),
                _ => "no click nearby".to_owned(),
            };
            out.push_str(&format!(
                "   #{:<5} {:<8} {:>9.0}ms  {where_}\n",
                miss.object_index, miss.kind, miss.time_ms
            ));
        }

        let summary = MissSummary::of(&self.misses);
        out.push_str(&format!(
            "   {} miss(es): {} circle, {} slider, {} spinner; {} with a click nearby, {} just outside\n",
            self.misses.len(),
            summary.circles,
            summary.sliders,
            summary.spinners,
            summary.with_nearby_click,
            summary.geometry_suspects,
        ));
        out
    }

    fn verdict(&self) -> String {
        if self.is_exact() {
            return "exact match".to_owned();
        }
        let mut parts = Vec::new();
        if !self.check.counts_match() {
            parts.push(format!(
                "counts off by {}",
                diff_summary(self.check.ours, self.check.theirs)
            ));
        }
        if !self.check.combo_matches() {
            parts.push(format!(
                "combo {:+}",
                i64::from(self.check.our_max_combo) - i64::from(self.check.their_max_combo)
            ));
        }
        format!("MISMATCH: {}", parts.join(", "))
    }

    pub fn json(&self) -> String {
        let ours = self.check.ours;
        let theirs = self.check.theirs;
        format!(
            concat!(
                "{{\"replay\":{},\"map_source\":{},\"title\":{},\"player\":{},\"mods\":{},",
                "\"objects\":{},\"exact\":{},\"counts_match\":{},\"combo_match\":{},",
                "\"ours\":{},\"theirs\":{},",
                "\"our_max_combo\":{},\"their_max_combo\":{},",
                "\"our_accuracy\":{:.4},\"their_accuracy\":{:.4},\"misses\":{}}}"
            ),
            quote(&self.replay_path),
            quote(&self.map_source),
            quote(&self.title),
            quote(&self.player),
            quote(&self.mods),
            self.objects,
            self.is_exact(),
            self.check.counts_match(),
            self.check.combo_matches(),
            counts_json(ours),
            counts_json(theirs),
            self.check.our_max_combo,
            self.check.their_max_combo,
            self.our_accuracy,
            self.their_accuracy,
            MissSummary::of(&self.misses).json(),
        )
    }
}

fn diff_summary(ours: HitCounts, theirs: HitCounts) -> String {
    let mut parts = Vec::new();
    for (label, a, b) in [
        ("300", ours.count_300, theirs.count_300),
        ("100", ours.count_100, theirs.count_100),
        ("50", ours.count_50, theirs.count_50),
        ("miss", ours.count_miss, theirs.count_miss),
    ] {
        let delta = i64::from(a) - i64::from(b);
        if delta != 0 {
            parts.push(format!("{label} {delta:+}"));
        }
    }
    parts.join(" ")
}

fn counts_json(counts: HitCounts) -> String {
    format!(
        "{{\"300\":{},\"100\":{},\"50\":{},\"miss\":{}}}",
        counts.count_300, counts.count_100, counts.count_50, counts.count_miss
    )
}

/// Minimal JSON string escaping — enough for filenames, titles and player
/// names, which is all this program emits.
fn quote(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

pub fn error_json(replay_path: &str, message: &str) -> String {
    format!(
        "{{\"replay\":{},\"error\":{}}}",
        quote(replay_path),
        quote(message)
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use dossier_sim::Verification;

    fn sample() -> Report {
        let counts = HitCounts {
            count_300: 10,
            count_100: 2,
            count_50: 1,
            count_miss: 3,
            ..HitCounts::default()
        };
        Report {
            replay_path: "a.osr".into(),
            map_source: "songs/1.osz → hard.osu".into(),
            title: "Artist - Title [Insane]".into(),
            player: "tester".into(),
            mods: "HDHR".into(),
            objects: 16,
            our_accuracy: counts.accuracy_std(),
            their_accuracy: counts.accuracy_std(),
            check: Verification {
                ours: counts,
                theirs: counts,
                our_max_combo: 100,
                their_max_combo: 100,
            },
            misses: Vec::new(),
        }
    }

    fn miss(distance: f64, dt: Option<f64>) -> MissContext {
        MissContext {
            object_index: 0,
            kind: "circle",
            time_ms: 1000.0,
            press_dt_ms: dt,
            press_distance_px: dt.map(|_| distance),
            radius_px: 32.0,
        }
    }

    #[test]
    fn a_click_just_outside_the_circle_is_flagged_as_our_problem() {
        assert!(miss(35.0, Some(4.0)).looks_like_a_geometry_error());
        // Far away in space: the player was somewhere else entirely.
        assert!(!miss(200.0, Some(4.0)).looks_like_a_geometry_error());
        // Far away in time: a click meant for a different object.
        assert!(!miss(35.0, Some(250.0)).looks_like_a_geometry_error());
        // No click at all: the player's miss, not ours.
        assert!(!miss(35.0, None).looks_like_a_geometry_error());
    }

    #[test]
    fn the_summary_separates_our_misses_from_the_players() {
        let summary = MissSummary::of(&[
            miss(34.0, Some(2.0)),
            miss(36.0, Some(-3.0)),
            miss(300.0, None),
        ]);
        assert_eq!(summary.circles, 3);
        assert_eq!(summary.with_nearby_click, 2);
        assert_eq!(summary.geometry_suspects, 2);
        // Overshoots are 2.0 and 4.0; the median of an even count takes the
        // upper of the two, which is fine for a diagnostic.
        assert_eq!(summary.median_overshoot_px, Some(4.0));
    }

    /// The bot parses this JSON by key. Renaming one here without renaming it
    /// there breaks a feature that no Rust test would otherwise notice.
    #[test]
    fn the_json_carries_every_key_the_bot_reads() {
        let json = sample().json();
        for key in [
            "\"exact\"",
            "\"player\"",
            "\"mods\"",
            "\"objects\"",
            "\"ours\"",
            "\"theirs\"",
            "\"our_max_combo\"",
            "\"their_max_combo\"",
            "\"our_accuracy\"",
            "\"their_accuracy\"",
            "\"300\"",
            "\"100\"",
            "\"50\"",
            "\"miss\"",
        ] {
            assert!(json.contains(key), "missing {key} in {json}");
        }
    }

    #[test]
    fn a_mismatch_is_named_rather_than_just_flagged() {
        let mut report = sample();
        report.check.theirs.count_300 = 11;
        report.check.theirs.count_miss = 2;
        let text = report.human();
        assert!(text.contains("MISMATCH"), "{text}");
        assert!(text.contains("300 -1"), "{text}");
        assert!(text.contains("miss +1"), "{text}");
    }

    #[test]
    fn quoting_escapes_what_would_break_the_line() {
        // Filenames really do contain quotes and backslashes, and one bad line
        // would take the whole read-out down with it.
        assert_eq!(quote(r#"a "b" \c"#), r#""a \"b\" \\c""#);
        assert_eq!(quote("line\nbreak"), r#""line\nbreak""#);
    }
}
