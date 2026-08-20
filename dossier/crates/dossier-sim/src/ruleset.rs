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

/// Which client wrote a replay.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Client {
    /// osu!stable — danser's reimplementation, lazer's Classic mod, and the
    /// corpus of stable replays.
    Stable,
    /// osu!lazer — `ppy/osu`, read directly.
    Lazer,
}

/// The rules a replay is judged by.
///
/// Not simply "which client", because lazer's Classic mod puts stable's rules
/// back one at a time. Its switches are independent and default to on, so a
/// lazer score with Classic can have stable's note lock and lazer's sliders, or
/// the reverse. Holding this as a client with a handful of switches says that;
/// holding it as a two-valued enum said something that is not true.
///
/// The switches are read from the block lazer appends to the replay — see
/// `Replay::lazer_mods`. Nothing in the corpus has Classic on, so the wiring
/// below is right by construction and unverified by measurement.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ruleset {
    client: Client,
    /// stable's wide note lock and everything that hangs off its hit policy.
    legacy_note_lock: bool,
    /// A slider carries its own verdict rather than deferring to its head, and
    /// its pieces are tracked stable's way — no handover from the head, no
    /// window on the tail.
    whole_sliders: bool,
    /// Whether the 300/100/50 a slider reports is its head's, read off the
    /// ordinary hit windows.
    ///
    /// Held apart from [`whole_sliders`](Self::whole_sliders) because stable's
    /// ScoreV2 moves this one and only this one. Folding the two together made
    /// a ScoreV2 replay inherit lazer's tail leniency and lazer's handover from
    /// the head as well, neither of which stable has under any mod.
    head_carries_verdict: bool,
    /// stable's health model, which solves for the drain rather than stating
    /// it.
    legacy_health: bool,
    /// Which generation of lazer's mod multipliers the score was computed
    /// with. Meaningless on stable, which has its own table and has never
    /// changed it.
    multipliers: crate::multiplier::Generation,
}

/// Replays written by a client at or above this version came out of lazer.
///
/// Stable's versions are dates: `20260711` is the 11th of July 2026. lazer
/// numbers its replays from 30000000 instead, which leaves the two ranges
/// unable to collide for the next eight hundred years.
const FIRST_LAZER_VERSION: i32 = 30_000_000;

impl Ruleset {
    pub const STABLE: Self = Self {
        client: Client::Stable,
        legacy_note_lock: true,
        whole_sliders: true,
        head_carries_verdict: false,
        legacy_health: true,
        multipliers: crate::multiplier::Generation::V2,
    };

    pub const LAZER: Self = Self {
        client: Client::Lazer,
        legacy_note_lock: false,
        whole_sliders: false,
        head_carries_verdict: true,
        legacy_health: false,
        multipliers: crate::multiplier::Generation::V2,
    };

    /// Read off the replay header's client version.
    pub fn of_replay_version(game_version: i32) -> Self {
        if game_version >= FIRST_LAZER_VERSION {
            Self::LAZER
        } else {
            Self::STABLE
        }
    }

    /// The same, plus whatever the Classic mod turns back on.
    ///
    /// ```csharp
    /// public Bindable<bool> NoSliderHeadAccuracy { get; } = new BindableBool(true);
    /// public Bindable<bool> ClassicNoteLock { get; } = new BindableBool(true);
    /// public Bindable<bool> ClassicHealth { get; } = new Bindable<bool>(true);
    /// ```
    ///
    /// Each is a switch a player can turn off on its own, which is why they are
    /// three fields and not one. All three default to on, so a setting the
    /// replay does not mention is on — absent is not false.
    pub fn of_replay(replay: &dossier_replay::Replay) -> Self {
        let mut ruleset = Self::of_replay_version(replay.game_version);
        // stable's ScoreV2 makes a slider worth what its head was worth. Only
        // the verdict: the slide is still tracked stable's way, so this is not
        // `whole_sliders` and must not be — see `head_carries_verdict`.
        if ruleset.client == Client::Stable && replay.mods.contains(dossier_replay::bits::SCORE_V2) {
            ruleset.head_carries_verdict = true;
        }
        // A replay carries the score its client computed at the time, and
        // lazer's mod multipliers were rebalanced under it. Reading an older
        // replay with today's table is not a rounding error — see
        // [`crate::multiplier`]. Only asked of lazer: stable's own table has
        // never moved, and setting this from a stable replay's date stamp
        // would be reading a number out of a field that does not hold one.
        if ruleset.client == Client::Lazer {
            ruleset.multipliers =
                crate::multiplier::Generation::of_replay_version(replay.game_version);
        }
        if let Some(classic) = replay.lazer_mods().iter().find(|m| m.acronym == "CL") {
            ruleset.legacy_note_lock = classic.switch("classic_note_lock", true);
            ruleset.whole_sliders = classic.switch("no_slider_head_accuracy", true);
            // The mod's name is the verdict question — "no slider head
            // accuracy" — and the tracking follows it, so one setting moves
            // both. They are still two fields, because stable's ScoreV2 moves
            // one of them without the other.
            ruleset.head_carries_verdict = !ruleset.whole_sliders;
            ruleset.legacy_health = classic.switch("classic_health", true);
        }
        ruleset
    }

    pub fn client(self) -> Client {
        self.client
    }

    /// Which generation of lazer's mod multipliers applies.
    pub fn multipliers(self) -> crate::multiplier::Generation {
        self.multipliers
    }

    /// Whether health is modelled stable's way — solved for, rather than
    /// stated. lazer's Classic mod restores it.
    pub fn legacy_health(self) -> bool {
        self.legacy_health
    }

    pub fn name(self) -> &'static str {
        match (self.client, self.legacy_note_lock || self.whole_sliders) {
            (Client::Stable, _) => "stable",
            (Client::Lazer, false) => "lazer",
            (Client::Lazer, true) => "lazer (classic)",
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
    /// Whether a live spinner takes a press that lands anywhere on screen.
    ///
    /// stable's spinner answers its hittability test with the time gates alone:
    /// the implementation uses neither the cursor position nor the radius, so
    /// while it is live it says yes to any press, and being earlier in the list
    /// it takes that press before anything behind it can. Read out of
    /// `osu!.exe` — see `docs/stable-client.md` for the route.
    ///
    /// Measured on the corpus and it moves nothing: 73 exact of 145 either way,
    /// to the digit. A press during a live spinner that would otherwise have
    /// reached a circle does not occur in 145 replays, which is what one would
    /// expect — spinning is a held key rather than a stream of new ones. It is
    /// here because it is what the client does, not because anything measurable
    /// turns on it.
    pub fn spinner_swallows_presses(self) -> bool {
        self.client == Client::Stable
    }

    pub fn blocks(
        self,
        blocker_end_ms: f64,
        blocker_start_ms: f64,
        target_start_ms: f64,
        press_time_ms: f64,
    ) -> bool {
        if self.legacy_note_lock {
            blocker_end_ms + STABLE_NOTELOCK_SLACK_MS < target_start_ms
        } else {
            press_time_ms < blocker_start_ms
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
    /// Both hang off one flag, `ClassicSliderBehaviour`, and lazer's Classic
    /// mod sets it from `NoSliderHeadAccuracy` — so a lazer score played with
    /// Classic scores its sliders stable's way. That mod has no legacy bit and
    /// cannot be seen in the header's mod field; it is read from the block
    /// lazer appends after it.
    pub fn slider_is_scored_by_its_head(self) -> bool {
        !self.whole_sliders
    }

    /// Whether the 300/100/50 a slider reports is its head's, on the ordinary
    /// windows, rather than a summary of how many pieces were caught.
    ///
    /// True for lazer, where the slider itself is `IgnoreHit`. Also true on
    /// **stable under ScoreV2**, which is the one thing that mod changes about
    /// judgement — and it changes only this. A slider tracked from end to end
    /// off a head hit forty milliseconds late is a 100 either way; what stays
    /// stable's is everything about *how* it is tracked, because ScoreV2 is a
    /// scoring mod and does not touch the follow circle.
    pub fn slider_verdict_from_head(self) -> bool {
        self.head_carries_verdict
    }

    /// Whether the pieces still have a say once the head has spoken.
    ///
    /// lazer's slider is worth exactly its head and nothing else — its ticks
    /// and its tail are judgements in their own right, counted separately, so
    /// dropping one cannot reach back and spoil the head's 300.
    ///
    /// stable under ScoreV2 has no separate counters to put them in: the header
    /// carries four numbers and a slider is one object. So both facts have to
    /// land on that one verdict, and the verdict is the worse of them — a
    /// perfect head on a slider that let go of its tail is a 100.
    ///
    /// Being measured, not quoted. Under the head alone the 50s and the misses
    /// came right and twenty-one sliders stayed 300 against the replay's 100,
    /// all of them with a dropped tail.
    pub fn slider_verdict_also_needs_its_pieces(self) -> bool {
        self.client == Client::Stable && self.head_carries_verdict
    }

    pub fn writes_off_stranded_notes(self) -> bool {
        !self.legacy_note_lock
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
        self.legacy_note_lock
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
        assert_eq!(Ruleset::of_replay_version(20_260_412), Ruleset::STABLE);
        assert_eq!(Ruleset::of_replay_version(20_230_206), Ruleset::STABLE);
        assert_eq!(Ruleset::of_replay_version(30_000_016), Ruleset::LAZER);
        assert_eq!(Ruleset::of_replay_version(30_000_018), Ruleset::LAZER);
    }

    fn replay_with(version: i32, mods: Vec<dossier_replay::LazerMod>) -> dossier_replay::Replay {
        dossier_replay::Replay {
            mode: dossier_replay::GameMode::Standard,
            game_version: version,
            beatmap_hash: String::new(),
            player: String::new(),
            replay_hash: String::new(),
            hits: dossier_replay::HitCounts::default(),
            score: 0,
            max_combo: 0,
            perfect_combo: false,
            mods: dossier_replay::Mods::new(0),
            life_bar: String::new(),
            timestamp_ticks: 0,
            online_score_id: 0,
            target_practice_accuracy: None,
            frames: Vec::new(),
            rng_seed: None,
            score_info: (!mods.is_empty()).then(|| dossier_replay::ScoreInfo {
                mods,
                ..dossier_replay::ScoreInfo::default()
            }),
        }
    }

    fn classic(settings: &[(&str, bool)]) -> dossier_replay::LazerMod {
        dossier_replay::LazerMod {
            acronym: "CL".into(),
            settings: settings
                .iter()
                .map(|(k, v)| ((*k).to_owned(), dossier_replay::Setting::Bool(*v)))
                .collect(),
        }
    }

    #[test]
    fn the_classic_mod_puts_stables_rules_back_one_at_a_time() {
        // Not "a Classic score is a stable score". Each switch is separate and
        // they default to on, so a score can have stable's note lock and
        // lazer's sliders, or the reverse — and reading the mod as a single
        // flag would get one of the two wrong every time someone changed a
        // setting.
        let all_on = Ruleset::of_replay(&replay_with(30_000_016, vec![classic(&[])]));
        assert!(all_on.slider_swallows_notes_beneath(), "note lock restored");
        assert!(!all_on.slider_is_scored_by_its_head(), "sliders whole again");
        assert!(all_on.legacy_health());
        // Still a lazer score for everything the mod does not touch.
        assert_eq!(all_on.client(), Client::Lazer);

        let no_sliders = Ruleset::of_replay(&replay_with(
            30_000_016,
            vec![classic(&[("no_slider_head_accuracy", false)])],
        ));
        assert!(no_sliders.slider_swallows_notes_beneath(), "lock still on");
        assert!(
            no_sliders.slider_is_scored_by_its_head(),
            "but sliders are lazer's again"
        );

        let no_lock = Ruleset::of_replay(&replay_with(
            30_000_016,
            vec![classic(&[("classic_note_lock", false)])],
        ));
        assert!(!no_lock.slider_swallows_notes_beneath());
        assert!(!no_lock.slider_is_scored_by_its_head(), "sliders still whole");
    }

    fn stable_replay_with_mods(mods: u32) -> dossier_replay::Replay {
        let mut replay = replay_with(20_260_412, Vec::new());
        replay.mods = dossier_replay::Mods::new(mods);
        replay
    }

    #[test]
    fn score_v2_moves_a_stable_sliders_verdict_and_nothing_about_its_tracking() {
        // The whole point of holding these apart. ScoreV2 is a scoring mod: it
        // makes a slider worth what its head was worth, and it does not give
        // stable lazer's handover from the head or lazer's window on the tail.
        // Folding both onto one flag would have handed a ScoreV2 replay two
        // tracking rules that no build of stable has ever had.
        let v2 = Ruleset::of_replay(&stable_replay_with_mods(dossier_replay::bits::SCORE_V2));
        assert_eq!(v2.client(), Client::Stable);
        assert!(v2.slider_verdict_from_head(), "the head decides the verdict");
        assert!(
            !v2.slider_is_scored_by_its_head(),
            "and the slide is still tracked stable's way"
        );
        // Everything else about stable is untouched.
        assert!(v2.legacy_health());
        assert!(v2.slider_swallows_notes_beneath());
    }

    #[test]
    fn a_stable_replay_without_score_v2_keeps_whole_sliders() {
        let plain = Ruleset::of_replay(&stable_replay_with_mods(0));
        assert!(!plain.slider_verdict_from_head());
        assert_eq!(plain, Ruleset::STABLE);

        // A neighbouring bit must not be mistaken for it.
        let hidden = Ruleset::of_replay(&stable_replay_with_mods(dossier_replay::bits::HIDDEN));
        assert!(!hidden.slider_verdict_from_head());
    }

    #[test]
    fn score_v2_on_a_lazer_replay_changes_nothing() {
        // lazer already scores a slider by its head, and its ScoreV2 mod is
        // about the scoring formula rather than the judgement. Reading the
        // legacy bit on a lazer replay and acting on it would be applying a
        // stable rule to a client that does not have it.
        // Past the multiplier rebalance, so this is `LAZER` exactly and the
        // comparison is about the mod and nothing else.
        let mut replay = replay_with(30_000_017, Vec::new());
        replay.mods = dossier_replay::Mods::new(dossier_replay::bits::SCORE_V2);
        assert_eq!(Ruleset::of_replay(&replay), Ruleset::LAZER);
    }

    #[test]
    fn a_lazer_replay_without_classic_keeps_lazers_rules() {
        let plain = Ruleset::of_replay(&replay_with(30_000_016, Vec::new()));
        assert_eq!(plain.client(), Client::Lazer);
        assert!(!plain.slider_swallows_notes_beneath());
        assert!(plain.slider_is_scored_by_its_head());
        assert!(!plain.legacy_health());

        let stable = Ruleset::of_replay(&replay_with(20_260_412, Vec::new()));
        assert_eq!(stable, Ruleset::STABLE);
    }

    #[test]
    fn the_replays_age_picks_which_multipliers_scored_it() {
        // The rules a replay is judged by come from which client wrote it; the
        // table its score was computed with comes from *when*. Those are two
        // questions and the header answers them separately.
        use crate::multiplier::Generation;
        let before = Ruleset::of_replay(&replay_with(30_000_016, Vec::new()));
        let after = Ruleset::of_replay(&replay_with(30_000_017, Vec::new()));
        assert_eq!(before.client(), after.client());
        assert_eq!(before.multipliers(), Generation::V1);
        assert_eq!(after.multipliers(), Generation::V2);
    }

    #[test]
    fn stable_blocks_a_late_press_where_lazer_does_not() {
        // The Camellia case: a note due at 64390 that ended there, a press at
        // 64427, and a target due at 64473. Stable blocks — the blocker ended
        // before the target started. Lazer does not — the press arrived after
        // the blocker was due, so it is no longer in the way.
        let (blocker_end, blocker_start, target_start, press) =
            (64_390.0, 64_390.0, 64_473.0, 64_427.0);
        assert!(Ruleset::STABLE.blocks(blocker_end, blocker_start, target_start, press));
        assert!(!Ruleset::LAZER.blocks(blocker_end, blocker_start, target_start, press));
    }

    #[test]
    fn lazer_blocks_a_press_that_arrives_before_the_blocker_is_due() {
        // The other half of lazer's rule: it does block, when the player is so
        // early that the note in the way has not even happened yet.
        assert!(Ruleset::LAZER.blocks(64_390.0, 64_390.0, 64_473.0, 64_100.0));
    }

    #[test]
    fn stable_ignores_a_blocker_that_overlaps_its_target() {
        // Two notes sharing an instant do not block each other — the slack is
        // what lets a 2B pattern be played at all.
        assert!(!Ruleset::STABLE.blocks(1_000.0, 1_000.0, 1_000.0, 1_000.0));
        assert!(!Ruleset::STABLE.blocks(1_000.0, 1_000.0, 1_002.0, 1_000.0));
        assert!(Ruleset::STABLE.blocks(1_000.0, 1_000.0, 1_004.0, 1_000.0));
    }
}
