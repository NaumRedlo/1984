//! The two rulesets, kept apart on purpose.
//!
//! osu!stable and osu!lazer do not judge the same play the same way, and the
//! difference is not a detail: on a desynced stream it is nine misses against
//! two hundred and thirty-two. A replay's header carries the version of the
//! client that wrote it, and every rule below is chosen by that version rather
//! than guessed at.
//!
//! ## Where each side comes from
//!
//! **lazer** is read straight out of `ppy/osu`. Every rule here names the file
//! and, where it is short enough, quotes it. There is no interpretation to do:
//! the ruleset is the source.
//!
//! **stable** is closed source, so it is assembled from reimplementations that
//! set out to match it — danser-go's `app/rulesets/osu/`, and lazer's own
//! Classic mod, which is ppy restoring stable behaviours and says in its
//! setting descriptions which behaviour each one is. Where those two agree the
//! answer is as settled as it can get without the binary. Where they are silent
//! the corpus decides: replays played on stable carry their own totals, and a
//! rule that disagrees with them is wrong whatever its provenance.
//!
//! ## What is deliberately shared
//!
//! Not everything differs, and inventing differences is as wrong as missing
//! them. The object model, the stacking, the slider path and the timing all
//! come from the beatmap and are the same under both. So is the shape of the
//! `.osr` header: lazer exports legacy counts, converting its own judgements
//! back into 300/100/50/miss, so both sides are compared against the same four
//! numbers and a slider stays one object with one verdict.

/// Which client's rules a replay is judged by.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ruleset {
    /// osu!stable — danser's reimplementation, lazer's Classic mod, and the
    /// corpus of stable replays.
    Stable,
    /// osu!lazer — `ppy/osu`, read directly.
    Lazer,
}

/// Replays written by a client at or above this version came out of lazer.
///
/// Stable's versions are dates: `20260711` is the 11th of July 2026. lazer
/// numbers its replays from 30000000 instead, which leaves the two ranges
/// unable to collide for the next eight hundred years.
const FIRST_LAZER_VERSION: i32 = 30_000_000;

impl Ruleset {
    /// Read off the replay header's client version.
    pub fn of_replay_version(game_version: i32) -> Self {
        if game_version >= FIRST_LAZER_VERSION {
            Self::Lazer
        } else {
            Self::Stable
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Stable => "stable",
            Self::Lazer => "lazer",
        }
    }

    /// Whether an earlier unjudged object stops a click reaching a later one.
    ///
    /// **stable** blocks outright, and the block is wide. `LegacyHitPolicy`:
    ///
    /// ```csharp
    /// if (testObject.HitObject.GetEndTime() + 3 < hitObject.HitObject.StartTime)
    ///     return ClickAction.Shake;
    /// ```
    ///
    /// Any earlier unjudged object that *ended* before this one started, with
    /// three milliseconds of slack for objects a hair unsnapped. danser
    /// implements the same rule.
    ///
    /// **lazer** blocks far less. `StartTimeOrderedHitPolicy`:
    ///
    /// ```csharp
    /// if (!blockingObject.Judged && time < blockingObject.HitObject.StartTime)
    ///     return ClickAction.Shake;
    /// ```
    ///
    /// Only a press that arrives *before* the blocking note was even due. Once
    /// its moment has passed it stops standing in the way — and
    /// [`writes_off_stranded_notes`](Self::writes_off_stranded_notes) disposes
    /// of it instead.
    pub fn blocks(
        self,
        blocker_end_ms: f64,
        blocker_start_ms: f64,
        target_start_ms: f64,
        press_time_ms: f64,
    ) -> bool {
        match self {
            Self::Stable => blocker_end_ms + STABLE_NOTELOCK_SLACK_MS < target_start_ms,
            Self::Lazer => press_time_ms < blocker_start_ms,
        }
    }

    /// Whether landing a click writes off every note still unjudged behind it.
    ///
    /// **lazer** does. `StartTimeOrderedHitPolicy.HandleHit` misses everything
    /// up to the object that was hit, there and then.
    ///
    /// **stable** does not — `LegacyHitPolicy.HandleHit` is empty:
    ///
    /// ```csharp
    /// public void HandleHit(DrawableHitObject hitObject)
    /// {
    /// }
    /// ```
    ///
    /// A note nobody reached waits for its own window to shut before it counts
    /// as missed. The difference is only ever *when* — but when is what a combo
    /// is made of, because notes clicked in the meantime count into the run
    /// first.
    /// Whether a slider's verdict is its head's, rather than a summary of its
    /// pieces.
    ///
    /// lazer took the slider apart. Its head is an ordinary circle with
    /// ordinary windows, its ticks and its end are judgements in their own
    /// right, and the slider itself is `IgnoreHit` — worth nothing and counted
    /// as nothing. So the 300 or 100 that reaches the scoreboard for a slider
    /// is the head's, and a slider tracked perfectly from a head hit forty
    /// milliseconds late is a 100.
    ///
    /// stable does the opposite: the head is worth a flat thirty whenever it
    /// lands, and the slider's own verdict comes from the fraction of its
    /// pieces that were caught.
    ///
    /// ```csharp
    /// // Slider.cs
    /// public override Judgement CreateJudgement() => ClassicSliderBehaviour
    ///     ? new OsuJudgement()
    ///     : new OsuIgnoreJudgement();
    ///
    /// // SliderHeadCircle.cs
    /// public override Judgement CreateJudgement() =>
    ///     ClassicSliderBehaviour ? new SliderTickJudgement() : base.CreateJudgement();
    /// ```
    ///
    /// Both hang off one flag, and lazer's Classic mod sets it — a lazer score
    /// played with Classic scores its sliders stable's way. We cannot see that
    /// mod: it has no legacy bit, and the `.osr` header's mod field is the
    /// legacy bitmask. A Classic lazer replay will be judged as though it were
    /// an ordinary one, which is wrong and currently undetectable.
    pub fn slider_is_scored_by_its_head(self) -> bool {
        self == Self::Lazer
    }

    pub fn writes_off_stranded_notes(self) -> bool {
        self == Self::Lazer
    }

    /// Whether a slider still travelling swallows a click on a note beneath it.
    ///
    /// **stable** keeps the head's hit area alive for the length of the slide:
    ///
    /// ```csharp
    /// slider.HitArea.CanBeHit = () => !slider.DrawableSlider.AllJudged;
    /// ```
    ///
    /// This lives in lazer's Classic mod under `ClassicNoteLock`, described as
    /// blocking input to objects underneath slider heads until the slider is
    /// fully judged — so it is stable's, restored, and not lazer's own.
    ///
    /// Only a 2B map puts a note under a travelling slider, so neither side of
    /// the corpus can measure this. It is modelled because the rule is known,
    /// not because it was needed.
    pub fn slider_swallows_notes_beneath(self) -> bool {
        self == Self::Stable
    }

    /// How far from a note a click can be and still be *an attempt at it* —
    /// judged, and judged a miss if it falls outside the 50 window, which
    /// takes the note with it. Outside this the note is not accepting input at
    /// all and the game shakes rather than consuming anything.
    ///
    /// Shared: `MISS_WINDOW = 400`, a half-width compared directly, since
    /// `WindowFor` is documented as "the number of +/- milliseconds allowed".
    ///
    /// It was briefly suspected of being narrower on stable, because a click
    /// 362ms early was eating a note the game left alone. Measuring the
    /// threshold found a corpus optimum around 310-360 — and no principle
    /// behind it. The real answer was somewhere else entirely: see
    /// [`slider_swallows_notes_beneath`](Self::slider_swallows_notes_beneath).
    /// A number tuned to two clicks would have buried it.
    pub fn hittable_range_ms(self) -> f64 {
        400.0
    }
}

/// Slack in stable's note lock, in milliseconds.
///
/// `LegacyHitPolicy` uses a literal `+ 3`. It only ever decides anything on 2B
/// patterns: on a map whose objects do not overlap in time, every earlier
/// object ended before the next one started and the tolerance is never
/// consulted.
const STABLE_NOTELOCK_SLACK_MS: f64 = 3.0;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_header_version_picks_the_ruleset() {
        assert_eq!(Ruleset::of_replay_version(20_260_412), Ruleset::Stable);
        assert_eq!(Ruleset::of_replay_version(20_230_206), Ruleset::Stable);
        assert_eq!(Ruleset::of_replay_version(30_000_016), Ruleset::Lazer);
        assert_eq!(Ruleset::of_replay_version(30_000_018), Ruleset::Lazer);
    }

    #[test]
    fn stable_blocks_a_late_press_where_lazer_does_not() {
        // The Camellia case: a note due at 64390 that ended there, a press at
        // 64427, and a target due at 64473. Stable blocks — the blocker ended
        // before the target started. Lazer does not — the press arrived after
        // the blocker was due, so it is no longer in the way.
        let (blocker_end, blocker_start, target_start, press) =
            (64_390.0, 64_390.0, 64_473.0, 64_427.0);
        assert!(Ruleset::Stable.blocks(blocker_end, blocker_start, target_start, press));
        assert!(!Ruleset::Lazer.blocks(blocker_end, blocker_start, target_start, press));
    }

    #[test]
    fn lazer_blocks_a_press_that_arrives_before_the_blocker_is_due() {
        // The other half of lazer's rule: it does block, when the player is so
        // early that the note in the way has not even happened yet.
        assert!(Ruleset::Lazer.blocks(64_390.0, 64_390.0, 64_473.0, 64_100.0));
    }

    #[test]
    fn stable_ignores_a_blocker_that_overlaps_its_target() {
        // Two notes sharing an instant do not block each other — the slack is
        // what lets a 2B pattern be played at all.
        assert!(!Ruleset::Stable.blocks(1_000.0, 1_000.0, 1_000.0, 1_000.0));
        assert!(!Ruleset::Stable.blocks(1_000.0, 1_000.0, 1_002.0, 1_000.0));
        assert!(Ruleset::Stable.blocks(1_000.0, 1_000.0, 1_004.0, 1_000.0));
    }
}
