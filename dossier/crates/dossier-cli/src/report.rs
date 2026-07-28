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
    /// Sliders whose tail survived only on the lenience window.
    pub lenient_tails: usize,
    /// …and those credited out at the rim of the follow circle.
    pub tails_near_the_rim: usize,
    /// Combo a flawless play would reach, by our count of the parts.
    pub max_possible_combo: u32,
    /// Our combo runs, longest first — only interesting when the combo
    /// disagrees, and then it is the fastest way to the object responsible.
    pub combo_chains: Vec<dossier_sim::ComboChain>,
    /// The two objects the game's extra break can have fallen on.
    pub combo_suspects: Vec<dossier_sim::Suspect>,
    /// What became of every press in the replay.
    pub presses: dossier_sim::PressSummary,
    /// …and the same presses one by one, for reading a window of the play.
    pub press_detail: Vec<dossier_sim::PressDetail>,
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
    /// Across failed spinners: turns swept against turns demanded. The ratio
    /// says which side is wrong — a consistent fraction points at the
    /// requirement, a near-zero one points at the counting.
    pub spin_rotations: Option<f64>,
    pub spin_required: Option<f64>,
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
            spin_rotations: mean(misses.iter().filter_map(|m| m.spin_rotations)),
            spin_required: mean(misses.iter().filter_map(|m| m.spin_required)),
        }
    }

    fn json(&self) -> String {
        format!(
            concat!(
                "{{\"circle\":{},\"slider\":{},\"spinner\":{},\"with_nearby_click\":{},",
                "\"geometry_suspects\":{},\"median_overshoot_px\":{},",
                "\"spin_rotations\":{},\"spin_required\":{}}}"
            ),
            self.circles,
            self.sliders,
            self.spinners,
            self.with_nearby_click,
            self.geometry_suspects,
            number(self.median_overshoot_px),
            number(self.spin_rotations),
            number(self.spin_required),
        )
    }
}

fn mean(values: impl Iterator<Item = f64>) -> Option<f64> {
    let collected: Vec<f64> = values.collect();
    if collected.is_empty() {
        return None;
    }
    Some(collected.iter().sum::<f64>() / collected.len() as f64)
}

fn number(value: Option<f64>) -> String {
    match value {
        Some(value) => format!("{value:.2}"),
        None => "null".to_owned(),
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
        out.push_str(&self.incomplete_play());
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

        out.push_str(&format!(
            "   full combo would be {} by our count\n",
            self.max_possible_combo
        ));
        out.push_str(&self.combo_runs());
        out.push_str(&self.combo_split());
        out.push_str(&self.early_break());
        out.push_str(&format!("   {}\n", self.verdict()));
        out
    }

    /// Our longest combo runs, and — when we hold a longer one than the replay
    /// does — the part that sits where the game must have broken.
    ///
    /// A combo that reads too high means the game broke somewhere we did not.
    /// The break has to fall inside our longest run, and it has to leave the
    /// game with its own maximum, which pins roughly where to look instead of
    /// leaving the whole map to search.
    /// Our combo runs, longest first — printed whenever the combo disagrees,
    /// in either direction, because the run that disagrees is the thing to go
    /// and look at and this is the only place its shape is visible.
    fn combo_runs(&self) -> String {
        let (ours, theirs) = (self.check.our_max_combo, self.check.their_max_combo);
        if ours == theirs || self.combo_chains.is_empty() {
            return String::new();
        }
        let mut out = format!("   our combo runs, longest first (theirs peaks at {theirs}):\n");
        for chain in self.combo_chains.iter().take(4) {
            let ended = match (chain.ended_at_ms.is_finite(), chain.part) {
                (true, Some(part)) => format!(
                    "ended at {:.0}ms on object #{}, its {part:?}",
                    chain.ended_at_ms, chain.object_index
                ),
                (true, None) => format!(
                    "ended at {:.0}ms on object #{}",
                    chain.ended_at_ms, chain.object_index
                ),
                (false, _) => "ran to the end of the play".to_owned(),
            };
            let over = if chain.length > theirs {
                format!("  ← {} longer than theirs", chain.length - theirs)
            } else {
                String::new()
            };
            out.push_str(&format!("      {:>5}  {ended}{over}\n", chain.length));
        }
        out
    }

    fn combo_split(&self) -> String {
        let (ours, theirs) = (self.check.our_max_combo, self.check.their_max_combo);
        if ours <= theirs || self.combo_chains.is_empty() {
            return String::new();
        }
        let mut out = String::new();
        // The two-candidate arithmetic only holds if the game broke exactly
        // once more than we did. Every object we scored above the game is a
        // break it may have taken and we did not, so more than one of those
        // and the split could be anywhere.
        let generous =
            u32::from(self.check.ours.count_300).saturating_sub(self.check.theirs.count_300.into());
        if !self.combo_suspects.is_empty() {
            if generous > 1 {
                out.push_str(&format!(
                    "   we scored {generous} objects above the game, so it may have broken more\n   than once — these two are only the answer if it broke once:\n"
                ));
            } else {
                out.push_str("   the game's break has to be at one of:\n");
            }
            for s in &self.combo_suspects {
                let click = match (s.press_dt_ms, s.press_distance_px) {
                    (Some(dt), Some(distance)) => format!(
                        "click {dt:+.0}ms, {distance:.1}px from centre (radius {:.1})",
                        s.radius_px
                    ),
                    _ => "no click near it".to_owned(),
                };
                out.push_str(&format!(
                    "      #{} {} at {:.0}ms — we said {:?}, {click}\n",
                    s.object_index, s.kind, s.time_ms, s.ours
                ));
            }
        }
        out
    }

    /// The mirror case: our combo reads too *low*, so we broke a run the game
    /// held together.
    ///
    /// There is no two-candidate arithmetic to run in this direction — the
    /// game's run is the longer one, so it contains ours — but that is exactly
    /// what pins the answer when the gap is a single part: our run sits inside
    /// theirs, so our extra break is at one of its two ends. Either the part
    /// that ended our run, or the one that ended the run before it and should
    /// not have.
    fn early_break(&self) -> String {
        let (ours, theirs) = (self.check.our_max_combo, self.check.their_max_combo);
        let Some(longest) = self.combo_chains.first() else {
            return String::new();
        };
        if ours >= theirs || longest.length != ours {
            return String::new();
        }

        let describe = |chain: &dossier_sim::ComboChain| match chain.part {
            Some(part) => format!(
                "object #{} at {:.0}ms, on its {part:?}",
                chain.object_index, chain.ended_at_ms
            ),
            None => "the end of the play — nothing broke it".to_owned(),
        };
        // The run that ended last before ours began: the break we took there is
        // what kept our run from starting a part earlier.
        let before = self
            .combo_chains
            .iter()
            .filter(|c| c.ended_at_ms < longest.ended_at_ms)
            .max_by(|a, b| a.ended_at_ms.total_cmp(&b.ended_at_ms));
        let mut out = format!(
            "   we broke {} time(s) the game did not — our longest run is {ours} to its {theirs}.\n",
            theirs - ours
        );
        if theirs - ours == 1 && self.check.counts_match() {
            match before {
                Some(before) => {
                    out.push_str(
                        "   If their run covers ours, the extra break is at one of its ends:\n",
                    );
                    out.push_str(&format!("      ours ended on {}\n", describe(longest)));
                    out.push_str(&format!(
                        "      the run before ours ended on {}\n",
                        describe(before)
                    ));
                }
                None => {
                    // Nothing ended before it, so the run starts where the play
                    // does and only one end is in question.
                    out.push_str(&format!(
                        "   Ours runs from the first object, so the break is where it ended:\n      {}\n",
                        describe(longest)
                    ));
                }
            }
        }
        out
    }

    /// Says so when the play ended before the map did, and over how much of it
    /// the numbers below were taken.
    ///
    /// A player whose health runs out stops being judged where they died, so
    /// the header accounts for fewer objects than the map has. Scored to the
    /// end regardless, such a play reads as hundreds of misses nobody made —
    /// a failed run of a 1127-object map came out 869 misses adrift. Both
    /// sides are therefore counted over the objects the play reached, which
    /// leaves a real comparison: the same objects, and the question of whether
    /// we judged them the way osu! did.
    fn incomplete_play(&self) -> String {
        if self.check.finished() {
            return String::new();
        }
        format!(
            "   this play ended early — {} of {} objects. Both columns below\n   \
             count only those, so the rest of the map is out of the comparison.\n\n",
            self.check.judged, self.check.objects
        )
    }

    /// Where every click in the replay went.
    ///
    /// The counts add up to the number of presses, which is the point: a play
    /// that scores badly can be asked *which* of the ways it went wrong rather
    /// than only how much. Runs of refusals matter more than the total — a
    /// scattered few are a player clicking early here and there, while a run is
    /// the note lock having lost the thread, and the timestamp says where to
    /// look.
    pub fn trace(&self, window: Option<(f64, f64)>) -> String {
        let p = &self.presses;
        if p.total() == 0 {
            return "   no presses to account for\n".to_owned();
        }
        let mut out = format!("   {} presses:\n", p.total());
        for (count, label) in [
            (p.landed, "landed"),
            (p.took_a_note_early, "took a note early"),
            (p.refused, "refused by the lock"),
            (p.out_of_range, "out of range"),
            (p.ignored, "ignored, stacked predecessor"),
            (p.found_nothing, "found nothing under the cursor"),
        ] {
            if count > 0 {
                out.push_str(&format!(
                    "      {count:>6}  {label} ({:.1}%)\n",
                    count as f64 / p.total() as f64 * 100.0
                ));
            }
        }
        if !p.refusal_runs.is_empty() {
            out.push_str("   the lock lost the thread at:\n");
            for (at, count) in p.refusal_runs.iter().take(8) {
                out.push_str(&format!(
                    "      {:>7.1}s  {count} clicks in a row\n",
                    at / 1000.0
                ));
            }
            if p.refusal_runs.len() > 8 {
                out.push_str(&format!(
                    "      …and {} more runs\n",
                    p.refusal_runs.len() - 8
                ));
            }
        }
        out.push_str(&self.presses_between(window));
        out
    }

    /// Every click inside a window, one line each.
    ///
    /// The totals say a play went wrong; a run of them says roughly where. This
    /// is the last step of that descent — the clicks themselves, with what each
    /// was tested against — and it is where every judgement question so far has
    /// actually been settled. Only inside a window, because a whole replay is
    /// thousands of lines and nobody reads those.
    fn presses_between(&self, window: Option<(f64, f64)>) -> String {
        let Some((from, to)) = window else {
            return String::new();
        };
        let mut out = format!("   clicks between {from:.0}ms and {to:.0}ms:\n");
        let mut shown = 0;
        for press in self
            .press_detail
            .iter()
            .filter(|p| p.time_ms >= from && p.time_ms <= to)
        {
            let target = match (press.object_index, press.error_ms, press.distance_px) {
                (Some(index), Some(error), Some(distance)) => format!(
                    "#{index} at {:.0}ms — {error:+.0}ms, {distance:.1}px of {:.1}",
                    press.object_ms.unwrap_or_default(),
                    press.radius_px
                ),
                _ => "nothing".to_owned(),
            };
            // How far back the blocker sits is the shape of a cascade: one
            // note behind is a player trailing their own stream, twenty is a
            // player mashing at a note they abandoned long ago.
            let blocker = match (press.blocked_by, press.object_index) {
                (Some(blocked_by), Some(index)) => {
                    format!(" ← blocked by #{blocked_by}, {} back", index - blocked_by)
                }
                _ => String::new(),
            };
            out.push_str(&format!(
                "      {:>8.0}ms  {:<20}  {target}{blocker}\n",
                press.time_ms,
                press.verdict.name()
            ));
            shown += 1;
        }
        if shown == 0 {
            out.push_str("      none\n");
        }
        out
    }

    /// Per-miss detail, for when the totals disagree and the question is why.
    pub fn explain(&self) -> String {
        if self.misses.is_empty() {
            return "   no misses to explain\n".to_owned();
        }
        let mut out = String::from("   our misses:\n");
        for miss in &self.misses {
            let where_ = match (miss.spin_rotations, miss.spin_required) {
                (Some(done), Some(needed)) => {
                    format!(
                        "{done:.1} of {needed:.1} turns ({:.0}%)",
                        done / needed * 100.0
                    )
                }
                _ => match (miss.press_dt_ms, miss.press_distance_px) {
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
                },
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
                "\"our_accuracy\":{:.4},\"their_accuracy\":{:.4},\"misses\":{},",
                "\"lenient_tails\":{},\"tails_near_the_rim\":{},",
                "\"max_possible_combo\":{}}}"
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
            self.lenient_tails,
            self.tails_near_the_rim,
            self.max_possible_combo,
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
                objects: 16,
                judged: 16,
            },
            misses: Vec::new(),
            lenient_tails: 0,
            tails_near_the_rim: 0,
            max_possible_combo: 0,
            combo_chains: Vec::new(),
            combo_suspects: Vec::new(),
            presses: dossier_sim::PressSummary::default(),
            press_detail: Vec::new(),
        }
    }

    #[test]
    fn a_play_that_ended_early_says_how_far_it_got() {
        // A player whose health runs out stops being judged where they died,
        // and the numbers are then taken over the part that happened. Reading
        // the table without knowing that would mean reading 40 objects as if
        // they were the whole map.
        let mut report = sample();
        report.objects = 100;
        report.check.objects = 100;
        report.check.judged = 40;
        let text = report.human();
        assert!(text.contains("ended early — 40 of 100 objects"), "{text}");
        assert!(text.contains("out of the comparison"), "{text}");
    }

    #[test]
    fn a_complete_play_says_nothing_about_ending_early() {
        let text = sample().human();
        assert!(!text.contains("ended early"), "{text}");
    }

    fn miss(distance: f64, dt: Option<f64>) -> MissContext {
        MissContext {
            object_index: 0,
            kind: "circle",
            time_ms: 1000.0,
            press_dt_ms: dt,
            press_distance_px: dt.map(|_| distance),
            radius_px: 32.0,
            spin_rotations: None,
            spin_required: None,
        }
    }

    fn spinner(done: f64, needed: f64) -> MissContext {
        MissContext {
            object_index: 0,
            kind: "spinner",
            time_ms: 1000.0,
            press_dt_ms: None,
            press_distance_px: None,
            radius_px: 32.0,
            spin_rotations: Some(done),
            spin_required: Some(needed),
        }
    }

    #[test]
    fn failed_spinners_report_how_far_short_they_fell() {
        let summary = MissSummary::of(&[spinner(10.0, 20.0), spinner(14.0, 20.0)]);
        assert_eq!(summary.spinners, 2);
        assert_eq!(summary.spin_rotations, Some(12.0));
        assert_eq!(summary.spin_required, Some(20.0));
        // A spinner has no click to blame, so it must never be counted as one.
        assert_eq!(summary.with_nearby_click, 0);
        assert_eq!(summary.geometry_suspects, 0);
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
