//! Turning a judged play into a hit-sound track.
//!
//! The sounds follow the *judgement*, not the map: a note makes its noise when
//! the player struck it, and a note nobody struck makes none. That is what osu!
//! does, and it's why the track can't be built from the beatmap alone — a
//! missed note in a rendered replay should be conspicuous by its silence.

use dossier_audio::{Kit, SamplePack, SampleSet, Track, Voice};
use dossier_beatmap::{sound_bits, Beatmap, HitObject, SampleSet as MapSet};
use dossier_sim::{GameState, Part};

/// The combo a break has to cost before it is worth a sound.
///
/// stable's `combobreak.wav` is not a miss sound: it fires when a *run* ends,
/// and only when the run was long enough to be worth mourning. That is what
/// keeps a mashed play from droning — eight hundred misses inside a play that
/// never gets past four combo make almost no noise, while one dropped note at
/// 400x is unmistakeable.
const COMBO_BREAK_THRESHOLD: u32 = 20;

/// Build the track for the span being rendered.
///
/// `at_video` maps a map instant to the video second it is seen at — the render
/// plan's own clock. Under DoubleTime that compresses the sounds along with
/// everything else; through a slow-motion dip it spreads them out. Each hit is
/// still a one-shot struck at that instant, so a slowed stretch spaces the hits
/// further apart without lowering any of their pitches — which is what a slowed
/// stretch should sound like.
pub fn build(
    state: &GameState,
    beatmap: &Beatmap,
    at_video: impl Fn(f64) -> f64,
    video_seconds: f64,
    kit: Kit,
    pack: SamplePack,
) -> Track {
    let mut track = Track::new(video_seconds, kit).with_samples(pack);
    let Some(judge) = state.judge() else {
        return track;
    };

    // The combo going into each event, so a break can be measured rather than
    // counted: `combo_after` is what the event left behind, and the run it
    // ended is the one carried in from the event before.
    let mut combo_before = 0u32;
    for event in judge.events() {
        let run = combo_before;
        combo_before = event.combo_after;

        if event.result.is_miss() {
            // osu! has no sound for a miss. It has one for *losing a run* —
            // `combobreak` — and that is the one worth having: it marks the
            // moment a play changed rather than every note that went past.
            if event.part.breaks_combo() && run >= COMBO_BREAK_THRESHOLD {
                track.strike_with(
                    Voice::Miss,
                    at_video(event.time_ms),
                    SampleSet::Normal,
                    1.0,
                );
            }
            continue;
        }
        let Some(object) = beatmap.objects.get(event.object_index) else {
            continue;
        };
        // Which edge of a slider this is, if it is one: the head is 0, each
        // repeat the next, and the tail the last. A mapper puts a finish on
        // the end and nothing on the head by writing exactly that.
        let edge = slider_edge(state, event.object_index, event.part, event.time_ms);
        let Some(voice) = voice_for(event.part, object, edge) else {
            continue;
        };
        let (set, index, volume) = bank_for(beatmap, object, voice, edge);
        track.strike_indexed(voice, at_video(event.time_ms), set, index, volume);
    }
    sustained(state, beatmap, &at_video, &mut track);
    track
}

/// The two sounds osu! *holds*: a slider's slide and a spinner's spin.
///
/// Not built from the events, because they are not events — an event is a
/// moment and these are spans. Held over the object's own span rather than over
/// the stretch the player was demonstrably tracking: a replay that drops a
/// slider halfway is rare, the sim does not publish a per-instant "was the ball
/// under the cursor" reading, and the failure this trades for is a sound that
/// runs a fraction of a second too long against one that never plays at all.
///
/// Skipped entirely for an object nobody played. A missed slider is silent in
/// osu! and has to be silent here, for the same reason a missed note is: the
/// silence is the information.
fn sustained(
    state: &GameState,
    beatmap: &Beatmap,
    at_video: &impl Fn(f64) -> f64,
    track: &mut Track,
) {
    let Some(judge) = state.judge() else {
        return;
    };
    // Which objects the player actually took part in. A slider whose every part
    // was missed never made a sound.
    let mut played = vec![false; beatmap.objects.len()];
    for event in judge.events() {
        if !event.result.is_miss() {
            if let Some(slot) = played.get_mut(event.object_index) {
                *slot = true;
            }
        }
    }

    for (index, object) in beatmap.objects.iter().enumerate() {
        if !played.get(index).copied().unwrap_or(false) {
            continue;
        }
        let Some(timed) = state.timeline().objects.get(index) else {
            continue;
        };
        let span = (at_video(timed.start_ms), at_video(timed.end_ms));

        match &object.kind {
            dossier_beatmap::ObjectKind::Slider(_) => {
                // The slide takes the bank of the object's *normal* sample and
                // the whistle its addition bank, which is what `With(...)` on
                // each of the two source samples comes to.
                let (set, bank, volume) = bank_for(beatmap, object, Voice::Normal, None);
                track.sustain(Voice::Slide, span, set, bank, volume, |_| 1.0);
                if object.hit_sound & sound_bits::WHISTLE != 0 {
                    let (set, bank, volume) =
                        bank_for(beatmap, object, Voice::Whistle, None);
                    track.sustain(Voice::SlideWhistle, span, set, bank, volume, |_| 1.0);
                }
            }
            dossier_beatmap::ObjectKind::Spinner { .. } => {
                let needed =
                    dossier_sim::required_spins(state.difficulty(), timed.duration_ms());
                let (set, bank, volume) = bank_for(beatmap, object, Voice::Normal, None);
                let held = timed.end_ms - timed.start_ms;
                track.sustain(Voice::Spin, span, set, bank, volume, |seconds| {
                    if needed <= 0.0 {
                        return SPIN_BASE_RATE;
                    }
                    // Where the *play* had got to at this point in the sound.
                    // Read off the cursor rather than off the clock, so a
                    // spinner nobody turned does not climb.
                    let at = timed.start_ms + f64::from(seconds as f32) * 1000.0;
                    let turned = dossier_sim::spinner_rotations(
                        state.cursor_track(),
                        timed.start_ms,
                        at.min(timed.start_ms + held),
                    );
                    let progress = (turned / needed) as f32;
                    (SPIN_BASE_RATE + progress * SPIN_RATE_RATIO).min(SPIN_MAX_RATE)
                });
            }
            _ => {}
        }
    }
}

/// How fast a `spinnerspin` is played back, as a multiple of its own speed.
///
/// ```csharp
/// private const float spinning_sample_modulated_base_frequency = 20_000f / 44_100;
/// private const float spinning_sample_modulaton_ratio = 40_000f / 44_100;
/// private const float spinning_sample_modulated_max_frequency = 100_000f / 44_100;
/// ```
///
/// Well under one at rest, so the recording is *stretched* — a spinner starts
/// low and climbs as it is turned, which is the whole character of the sound.
const SPIN_BASE_RATE: f32 = 20_000.0 / 44_100.0;
const SPIN_RATE_RATIO: f32 = 40_000.0 / 44_100.0;
const SPIN_MAX_RATE: f32 = 100_000.0 / 44_100.0;

/// Which edge of a slider a part belongs to, counted from the head.
///
/// Derived from the time rather than tracked, because the events already carry
/// it: every edge falls on a whole number of traversals from the start, so
/// dividing by the traversal length names it. Returns `None` for anything that
/// is not an edge — ticks, circles, spinners.
fn slider_edge(state: &GameState, index: usize, part: Part, time_ms: f64) -> Option<usize> {
    if !matches!(
        part,
        Part::SliderHead | Part::SliderRepeat | Part::SliderTail
    ) {
        return None;
    }
    let object = state.timeline().objects.get(index)?;
    let duration = object.slide_duration_ms()?;
    if duration <= 0.0 {
        return Some(0);
    }
    Some((((time_ms - object.start_ms) / duration).round().max(0.0)) as usize)
}

/// The sound bits in force for a part: the slider edge's own, when the map
/// gave that edge one, and otherwise the object's.
fn bits_for(object: &HitObject, edge: Option<usize>) -> u8 {
    match (&object.kind, edge) {
        (dossier_beatmap::ObjectKind::Slider(slider), Some(edge)) => slider
            .edge_sounds
            .get(edge)
            .copied()
            .unwrap_or(object.hit_sound),
        _ => object.hit_sound,
    }
}

/// Which sound a part of an object makes.
fn voice_for(part: Part, object: &HitObject, edge: Option<usize>) -> Option<Voice> {
    match part {
        // The slider's overall verdict is a score, not a strike, and a spinner
        // has no single moment to sound at.
        //
        // Its turns have moments and still get nothing. They arrive several a
        // second and osu! does not sound them; giving each one a strike turned
        // every spinner into a machine gun, which is what happens when a part
        // is added to the judge for the score's sake and the sound follows it
        // by default.
        Part::Slider | Part::Spinner => None,
        // The turns that pay nothing and the ones that pay their hundred stay
        // silent — they arrive several a second, and osu! sounds those with a
        // loop rather than a strike. A bonus turn is the exception: it happens
        // once the spinner is already complete, it is the one thing in a
        // spinner worth marking, and osu! has a file for exactly it.
        Part::SpinnerSpin | Part::SpinnerPoints => None,
        Part::SpinnerBonus => Some(Voice::Bonus),
        Part::SliderTick => Some(Voice::Tick),
        _ => Some(loudest(bits_for(object, edge))),
    }
}

/// Which bank the sound comes from, and how loud.
///
/// osu! resolves this in layers, and each one only speaks if it has something
/// to say. The note's own field wins; a zero there means the timing point
/// decides; and additions (whistle, finish, clap) have a bank of their own that
/// falls back to the plain one. Collapsing any of that loses a distinction the
/// mapper made on purpose.
/// The bank, the custom index within it, and how loud the hit is.
///
/// The index is the map switching between several sets of the same bank —
/// `soft-hitnormal.wav` against `soft-hitnormal2.wav`. It comes from the note
/// when the note names one and from the timing point otherwise, exactly as the
/// bank and the volume do. Ignoring it does not fall silent; it plays the
/// wrong sample, which is the kind of wrong nobody hears until they play the
/// map in the game.
fn bank_for(
    beatmap: &Beatmap,
    object: &HitObject,
    voice: Voice,
    edge: Option<usize>,
) -> (SampleSet, u32, f32) {
    let point = beatmap.timing.sample_point_at(object.time_ms);
    let inherited = point.map_or(MapSet::Normal, |p| p.set);

    let sample = object.hit_sample;
    // A slider edge may name its own banks, and they take precedence over the
    // object's for that edge alone.
    let (edge_normal, edge_addition) = match (&object.kind, edge) {
        (dossier_beatmap::ObjectKind::Slider(slider), Some(edge)) => slider
            .edge_sets
            .get(edge)
            .copied()
            .unwrap_or((sample.normal_set, sample.addition_set)),
        _ => (sample.normal_set, sample.addition_set),
    };
    let sample = dossier_beatmap::HitSample {
        normal_set: if edge_normal != 0 {
            edge_normal
        } else {
            sample.normal_set
        },
        addition_set: if edge_addition != 0 {
            edge_addition
        } else {
            sample.addition_set
        },
        ..sample
    };
    let code = match voice {
        // The plain hit and the slider tick follow the note's own bank.
        Voice::Normal | Voice::Tick => sample.normal_set,
        // Decorations have their own, falling back to the plain one.
        _ => {
            if sample.addition_set != 0 {
                sample.addition_set
            } else {
                sample.normal_set
            }
        }
    };
    let set = if code == 0 {
        inherited
    } else {
        MapSet::from_code(code)
    };

    let volume = if sample.volume > 0 {
        f32::from(sample.volume)
    } else {
        point.map_or(100.0, |p| f32::from(p.volume))
    } / 100.0;

    let index = if sample.index > 0 {
        sample.index
    } else {
        point.map_or(1, |p| p.index)
    };

    (convert(set), index.max(1), volume)
}

/// The beatmap's notion of a bank and the audio crate's are the same three
/// names held by two crates that have no business depending on each other.
fn convert(set: MapSet) -> SampleSet {
    match set {
        MapSet::Normal => SampleSet::Normal,
        MapSet::Soft => SampleSet::Soft,
        MapSet::Drum => SampleSet::Drum,
    }
}

/// osu! lets a note carry several sounds at once. Layering them all turns a
/// busy map into mush, so the most prominent one wins — which is also the one
/// the mapper put there to be noticed.
fn loudest(bits: u8) -> Voice {
    if bits & sound_bits::FINISH != 0 {
        Voice::Finish
    } else if bits & sound_bits::CLAP != 0 {
        Voice::Clap
    } else if bits & sound_bits::WHISTLE != 0 {
        Voice::Whistle
    } else {
        Voice::Normal
    }
}

/// A short piece for listening to a kit on its own.
///
/// Each voice in isolation, then all of them in a stream at 180bpm. The
/// isolated hits say what a sound *is*; the stream says whether it survives
/// being played fast, which is where most hit sounds fall apart.
pub fn audition(kit: Kit, pack: SamplePack) -> Track {
    // The pack has to be in place before the first strike: a voice is rendered
    // once and cached, so attaching samples afterwards would be ignored.
    let mut track = Track::new(6.0, kit).with_samples(pack);
    let mut at = 0.3;

    for voice in [
        Voice::Normal,
        Voice::Whistle,
        Voice::Finish,
        Voice::Clap,
        Voice::Tick,
    ] {
        for _ in 0..3 {
            track.strike(voice, at);
            at += 0.28;
        }
        at += 0.35;
    }

    // 1/4 notes at 180bpm — 83ms apart, the density that exposes a sound with
    // too long a tail.
    at += 0.3;
    for i in 0..24 {
        let voice = match i % 8 {
            0 => Voice::Finish,
            4 => Voice::Clap,
            2 | 6 => Voice::Whistle,
            _ => Voice::Normal,
        };
        track.strike(voice, at + f64::from(i) * 0.0833);
    }
    track
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_undecorated_note_makes_the_plain_sound() {
        assert_eq!(loudest(0), Voice::Normal);
        assert_eq!(loudest(sound_bits::NORMAL), Voice::Normal);
    }

    #[test]
    fn each_decoration_has_its_own_voice() {
        assert_eq!(loudest(sound_bits::WHISTLE), Voice::Whistle);
        assert_eq!(loudest(sound_bits::FINISH), Voice::Finish);
        assert_eq!(loudest(sound_bits::CLAP), Voice::Clap);
    }

    #[test]
    fn stacked_sounds_pick_one_rather_than_pile_up() {
        // Layering every bit on a note that carries three of them is how a
        // dense map turns into noise.
        let all = sound_bits::WHISTLE | sound_bits::FINISH | sound_bits::CLAP;
        assert_eq!(loudest(all), Voice::Finish);
        assert_eq!(loudest(sound_bits::WHISTLE | sound_bits::CLAP), Voice::Clap);
    }
}

#[cfg(test)]
mod banks {
    use super::*;
    fn map(timing_points: &str, object: &str) -> Beatmap {
        Beatmap::parse(&format!(
            "osu file format v14\n\n[TimingPoints]\n{timing_points}\n\n[HitObjects]\n{object}\n"
        ))
        .expect("test map should parse")
    }

    /// `time,beatLength,meter,sampleSet,sampleIndex,volume,uninherited,effects`
    const SOFT_AT_HALF: &str = "0,500,4,2,0,50,1,0";
    const DRUM_LOUD: &str = "0,500,4,3,0,100,1,0";

    #[test]
    fn a_note_that_says_nothing_takes_the_timing_points_bank_and_volume() {
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,0");
        let (set, _, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
        assert_eq!(set, SampleSet::Soft);
        assert!((volume - 0.5).abs() < 1e-6);
    }

    #[test]
    fn a_note_takes_the_timing_points_custom_bank() {
        // The fifth field of a timing point is the sample index: which of the
        // skin's several sets of the same bank to play. A map switches between
        // them mid-song, and ignoring it does not fall silent — it plays the
        // wrong sample, which nobody hears until they play the map in the game.
        let beatmap = map("0,500,4,2,3,50,1,0", "100,100,1000,1,0");
        let (_, index, _) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
        assert_eq!(index, 3);
    }

    #[test]
    fn a_notes_own_index_overrules_the_timing_points() {
        // `0:0:5:0:` — the third colon field is the note's own index.
        let beatmap = map("0,500,4,2,3,50,1,0", "100,100,1000,1,0,0:0:5:0:");
        let (_, index, _) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
        assert_eq!(index, 5);
    }

    #[test]
    fn saying_nothing_anywhere_means_the_skins_first_bank() {
        // Zero is "inherit", and with nothing to inherit from it is the
        // unsuffixed file — never index 0, which is not a file osu! writes.
        let beatmap = map("0,500,4,2,0,50,1,0", "100,100,1000,1,0");
        let (_, index, _) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
        assert_eq!(index, 1);
    }

    #[test]
    fn a_notes_own_bank_overrules_the_timing_point() {
        // `3:0:0:0:` — drum for the plain hit, everything else inherited.
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,0,3:0:0:0:");
        let (set, _, _) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
        assert_eq!(set, SampleSet::Drum);
    }

    #[test]
    fn decorations_have_a_bank_of_their_own() {
        // Normal from drum, additions from soft: a mapper can put the clap in
        // a different bank from the hit under it, and often does.
        let beatmap = map(DRUM_LOUD, "100,100,1000,1,8,3:2:0:0:");
        let object = &beatmap.objects[0];
        assert_eq!(
            bank_for(&beatmap, object, Voice::Normal, None).0,
            SampleSet::Drum
        );
        assert_eq!(
            bank_for(&beatmap, object, Voice::Clap, None).0,
            SampleSet::Soft
        );
    }

    #[test]
    fn a_decoration_with_no_bank_of_its_own_follows_the_plain_hit() {
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,8,3:0:0:0:");
        let object = &beatmap.objects[0];
        assert_eq!(
            bank_for(&beatmap, object, Voice::Clap, None).0,
            SampleSet::Drum
        );
    }

    #[test]
    fn a_notes_own_volume_overrules_the_timing_points() {
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,0,0:0:0:20:");
        let (_, _, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
        assert!((volume - 0.2).abs() < 1e-6, "got {volume}");
    }

    #[test]
    fn the_bank_follows_the_section_a_note_falls_in() {
        // Two sections, and a note in each. Reading only the first line would
        // play the whole map in one bank.
        let beatmap = map(
            "0,500,4,2,0,50,1,0\n5000,-100,4,3,0,90,0,0",
            "100,100,1000,1,0\n200,200,6000,1,0",
        );
        assert_eq!(
            bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None).0,
            SampleSet::Soft
        );
        assert_eq!(
            bank_for(&beatmap, &beatmap.objects[1], Voice::Normal, None).0,
            SampleSet::Drum,
            "a green line carries sound settings too"
        );
    }

    #[test]
    fn a_note_with_no_sample_field_at_all_still_resolves() {
        // Most notes on most maps say nothing, so this is the common path, not
        // the edge case.
        let beatmap = map(DRUM_LOUD, "100,100,1000,1,0");
        let (set, _, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
        assert_eq!(set, SampleSet::Drum);
        assert!((volume - 1.0).abs() < 1e-6);
    }
}

#[cfg(test)]
mod edge_tests {
    use super::*;
    use dossier_beatmap::ObjectKind;

    fn slider_map(edge_sounds: &str, edge_sets: &str) -> Beatmap {
        let extra = if edge_sets.is_empty() {
            format!(",{edge_sounds}")
        } else {
            format!(",{edge_sounds},{edge_sets}")
        };
        Beatmap::parse(&format!(
            "osu file format v14\n\n\
             [TimingPoints]\n0,500,4,1,0,100,1,0\n\n\
             [HitObjects]\n0,0,1000,2,0,L|100:0,2,100{extra}\n"
        ))
        .expect("test map should parse")
    }

    fn edges_of(map: &Beatmap) -> (Vec<u8>, Vec<(u8, u8)>) {
        match &map.objects[0].kind {
            ObjectKind::Slider(s) => (s.edge_sounds.clone(), s.edge_sets.clone()),
            _ => panic!("that was a slider"),
        }
    }

    #[test]
    fn a_slider_carries_a_sound_for_every_edge() {
        // `slides,length,edgeSounds,edgeSets` — one bitmask and one bank pair
        // per edge: the head, each repeat, then the tail. Two slides means
        // three edges.
        let map = slider_map("0|8|2", "0:0|1:2|0:0");
        let (sounds, sets) = edges_of(&map);
        assert_eq!(sounds, vec![0, 8, 2]);
        assert_eq!(sets, vec![(0, 0), (1, 2), (0, 0)]);
    }

    #[test]
    fn each_edge_makes_its_own_sound() {
        // The whole point of the field: a finish on the repeat and a whistle
        // on the tail, where before every edge made the object's one sound.
        let map = slider_map("0|4|2", "");
        let object = &map.objects[0];
        assert_eq!(
            voice_for(Part::SliderHead, object, Some(0)),
            Some(Voice::Normal)
        );
        assert_eq!(
            voice_for(Part::SliderRepeat, object, Some(1)),
            Some(Voice::Finish)
        );
        assert_eq!(
            voice_for(Part::SliderTail, object, Some(2)),
            Some(Voice::Whistle)
        );
    }

    #[test]
    fn an_edge_bank_overrules_the_objects_for_that_edge_alone() {
        // `1:2` on the middle edge: a normal bank of 1 and an addition bank of
        // 2, against a timing point saying Normal for everything else.
        let map = slider_map("0|4|0", "0:0|1:2|0:0");
        let object = &map.objects[0];
        assert_eq!(
            bank_for(&map, object, Voice::Finish, Some(1)).0,
            SampleSet::Soft,
            "the repeat's addition bank"
        );
        assert_eq!(
            bank_for(&map, object, Voice::Normal, Some(0)).0,
            SampleSet::Normal,
            "the head keeps the timing point's"
        );
    }

    #[test]
    fn a_slider_that_names_no_edges_falls_back_to_its_own_sound() {
        // Most sliders say nothing, and every edge should then sound the way
        // the object does — which is what happened before edges existed.
        let map = Beatmap::parse(
            "osu file format v14\n\n\
             [TimingPoints]\n0,500,4,1,0,100,1,0\n\n\
             [HitObjects]\n0,0,1000,2,4,L|100:0,2,100\n",
        )
        .expect("test map should parse");
        let object = &map.objects[0];
        for edge in 0..3 {
            assert_eq!(
                voice_for(Part::SliderTail, object, Some(edge)),
                Some(Voice::Finish),
                "edge {edge}"
            );
        }
    }
}

#[cfg(test)]
mod miss_tests {
    use super::*;
    use dossier_replay::{HitCounts, Keys, Mods, Replay, ReplayFrame};

    fn replay(frames: Vec<ReplayFrame>) -> Replay {
        Replay {
            mode: dossier_replay::GameMode::Standard,
            game_version: 20_260_101,
            beatmap_hash: String::new(),
            player: "t".into(),
            replay_hash: String::new(),
            hits: HitCounts::default(),
            score: 0,
            max_combo: 0,
            perfect_combo: false,
            mods: Mods::new(0),
            life_bar: String::new(),
            timestamp_ticks: 0,
            online_score_id: 0,
            target_practice_accuracy: None,
            frames,
            rng_seed: None,
            score_info: None,
        }
    }

    /// Whether a track carries any sound at all — read off the PCM, since
    /// that is the only thing a listener would get.
    fn audible(track: &Track) -> bool {
        track.to_pcm().chunks(2).any(|s| {
            let v = i16::from_le_bytes([s[0], *s.get(1).unwrap_or(&0)]);
            v.abs() > 8
        })
    }

    /// A map of `n` circles a second apart, all on one spot.
    fn circles(n: usize) -> Beatmap {
        let mut body = String::from(
            "osu file format v14\n\n[Difficulty]\nCircleSize:5\nOverallDifficulty:5\n\n[HitObjects]\n",
        );
        // Spread out, or they stack and the note lock starts deciding things
        // this test is not about.
        for i in 0..n {
            let x = 60 + (i % 8) * 50;
            let y = 60 + (i / 8) * 50;
            body.push_str(&format!("{x},{y},{},1,0\n", 1000 + i * 300));
        }
        Beatmap::parse(&body).unwrap()
    }

    #[test]
    fn a_short_run_of_misses_stays_silent() {
        // stable's combobreak only fires for a run worth mourning. A play that
        // drops two notes having never got going says nothing — which is what
        // keeps a mashed replay from droning through eight hundred misses.
        let map = circles(2);
        let frames = vec![ReplayFrame {
            time_ms: 0,
            x: 0.0,
            y: 0.0,
            keys: Keys(0),
        }];
        let state = GameState::new(&map, &replay(frames));
        let track = build(
            &state,
            &map,
            |map_ms| map_ms / 1000.0,
            5.0,
            dossier_audio::Kit::plain(),
            dossier_audio::SamplePack::default(),
        );
        assert!(!audible(&track), "two dropped notes are not a lost run");
    }

    #[test]
    fn losing_a_long_run_is_heard() {
        // Twenty-five circles clicked, then one dropped: that is a run ending,
        // and the one moment in a play worth a sound of its own.
        let map = circles(30);
        let mut frames = Vec::new();
        for i in 0..25usize {
            let at = (1000 + i * 300) as i64;
            let (x, y) = ((60 + (i % 8) * 50) as f32, (60 + (i / 8) * 50) as f32);
            frames.push(ReplayFrame { time_ms: at - 10, x, y, keys: Keys(0) });
            frames.push(ReplayFrame { time_ms: at, x, y, keys: Keys(Keys::K1) });
            frames.push(ReplayFrame { time_ms: at + 10, x, y, keys: Keys(0) });
        }
        frames.push(ReplayFrame { time_ms: 12_000, x: 0.0, y: 0.0, keys: Keys(0) });

        let state = GameState::new(&map, &replay(frames));
        let track = build(
            &state,
            &map,
            |map_ms| map_ms / 1000.0,
            20.0,
            dossier_audio::Kit::plain(),
            dossier_audio::SamplePack::default(),
        );
        let judge = state.judge().unwrap();
        assert!(
            judge.final_state().max_combo >= COMBO_BREAK_THRESHOLD,
            "the run has to be long enough to count: {}",
            judge.final_state().max_combo
        );
        // Measured where nothing else lands. The twenty-five hits sound too,
        // and `audible` over the whole track was true whatever the break did —
        // a test that could not fail. The last hit is at 8.2s and the next
        // object is the one that was dropped, so the window after it holds the
        // break and nothing else.
        let pcm = track.to_pcm();
        let frame = |seconds: f64| (seconds * 44_100.0) as usize * 4;
        let after = pcm[frame(8.6)..frame(9.6).min(pcm.len())]
            .chunks_exact(2)
            .map(|s| i16::from_le_bytes([s[0], s[1]]).abs())
            .max()
            .unwrap_or(0);
        assert!(after > 8, "losing it should be heard: {after}");
    }

    #[test]
    fn a_broken_run_is_heard_in_the_skins_own_voice() {
        // Bankless, like `spinnerbonus` and `spinnerspin`: osu! ships one
        // `combobreak.wav` for the whole skin. This was the one sound the
        // engine struck without ever asking the skin for it, so every render
        // broke combo in the synthesised voice while the file sat in the folder
        // unread.
        //
        // Asked as "does the skin's file change what is heard", which is a
        // question about the lookup rather than about the synthesiser: blank is
        // only distinguishable from missing if the file is being read at all.
        let dir = std::env::temp_dir().join(format!("dossier-break-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a folder");
        std::fs::write(dir.join("combobreak.wav"), []).expect("a blank");

        let map = circles(30);
        let mut frames = Vec::new();
        for i in 0..25usize {
            let at = (1000 + i * 300) as i64;
            let (x, y) = ((60 + (i % 8) * 50) as f32, (60 + (i / 8) * 50) as f32);
            frames.push(ReplayFrame { time_ms: at - 10, x, y, keys: Keys(0) });
            frames.push(ReplayFrame { time_ms: at, x, y, keys: Keys(Keys::K1) });
            frames.push(ReplayFrame { time_ms: at + 10, x, y, keys: Keys(0) });
        }
        frames.push(ReplayFrame { time_ms: 12_000, x: 0.0, y: 0.0, keys: Keys(0) });
        let state = GameState::new(&map, &replay(frames));

        // Total energy rather than a window. Every other sound in the two
        // tracks is the same — neither pack carries a hit sound, so both
        // synthesise all twenty-five — and the only thing that differs is
        // whether the break had a recording to play. Taking a recording away
        // takes energy with it; nothing else could.
        let energy = |pack: dossier_audio::SamplePack| -> u64 {
            let track = build(
                &state,
                &map,
                |map_ms| map_ms / 1000.0,
                20.0,
                dossier_audio::Kit::plain(),
                pack,
            );
            track
                .to_pcm()
                .chunks_exact(2)
                .map(|s| u64::from(i16::from_le_bytes([s[0], s[1]]).unsigned_abs()))
                .sum()
        };

        let synthesised = energy(dossier_audio::SamplePack::default());
        let blanked = energy(dossier_audio::SamplePack::load(&dir));
        assert!(synthesised > 0, "nothing was heard at all");
        assert!(
            blanked < synthesised,
            "the skin's own combobreak was not read: {blanked} against {synthesised}"
        );
    }
}

/// The two sounds that are held rather than struck.
#[cfg(test)]
mod held {
    use super::*;
    use dossier_replay::{Keys, Mods, Replay, ReplayFrame};

    fn replay(frames: Vec<ReplayFrame>) -> Replay {
        Replay {
            mode: dossier_replay::GameMode::Standard,
            game_version: 20_260_101,
            beatmap_hash: String::new(),
            player: "tester".into(),
            replay_hash: String::new(),
            hits: Default::default(),
            score: 0,
            max_combo: 0,
            perfect_combo: false,
            mods: Mods::default(),
            life_bar: String::new(),
            timestamp_ticks: 0,
            online_score_id: 0,
            target_practice_accuracy: None,
            frames,
            rng_seed: None,
            score_info: None,
        }
    }

    /// A folder holding `{name}.wav`: a fifth of a second of steady tone.
    fn samples_with(names: &[&str]) -> std::path::PathBuf {
        // Counted as well as named. Two tests that want the same sounds used to
        // be handed the same folder, and since they run at once one would empty
        // it while the other was reading — a failure that appeared and went
        // away depending on which order the runner felt like.
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "dossier-held-{}-{}-{}",
            names.join("-"),
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a folder");

        let frames = 44_100 / 5;
        let mut data = Vec::with_capacity(frames * 2);
        for n in 0..frames {
            let phase = n as f32 / 44_100.0 * 300.0 * std::f32::consts::TAU;
            data.extend_from_slice(&((phase.sin() * 12_000.0) as i16).to_le_bytes());
        }
        let mut wav = Vec::new();
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&(36 + data.len() as u32).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&44_100u32.to_le_bytes());
        wav.extend_from_slice(&88_200u32.to_le_bytes());
        wav.extend_from_slice(&2u16.to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&(data.len() as u32).to_le_bytes());
        wav.extend_from_slice(&data);
        for name in names {
            std::fs::write(dir.join(format!("{name}.wav")), &wav).expect("written");
        }
        dir
    }

    /// One slider, two seconds long, held from start to finish.
    fn slider_map(hit_sound: u8) -> Beatmap {
        Beatmap::parse(&format!(
            "osu file format v14\n\n[Difficulty]\nCircleSize:4\nApproachRate:5\n\
             SliderMultiplier:1.4\nSliderTickRate:1\n\n\
             [TimingPoints]\n0,500,4,1,0,100,1,0\n\n\
             [HitObjects]\n100,192,1000,2,{hit_sound},L|240:192,1,140\n"
        ))
        .expect("test map should parse")
    }

    fn held_replay() -> Replay {
        let mut frames = vec![ReplayFrame { time_ms: 900, x: 100.0, y: 192.0, keys: Keys(0) }];
        // Down on the head and following the ball to the end.
        for step in 0..=25i64 {
            let at = 1000 + step * 20;
            let along = step as f32 / 25.0;
            frames.push(ReplayFrame {
                time_ms: at,
                x: 100.0 + 140.0 * along,
                y: 192.0,
                keys: Keys(Keys::K1),
            });
        }
        frames.push(ReplayFrame { time_ms: 1600, x: 240.0, y: 192.0, keys: Keys(0) });
        replay(frames)
    }

    fn loudness(track: &dossier_audio::Track, from: f64, to: f64) -> i16 {
        let pcm = track.to_pcm();
        let frame = |seconds: f64| (seconds * 44_100.0) as usize * 4;
        pcm[frame(from)..frame(to).min(pcm.len())]
            .chunks_exact(2)
            .map(|s| i16::from_le_bytes([s[0], s[1]]).abs())
            .max()
            .unwrap_or(0)
    }

    fn track_for(dir: Option<&std::path::Path>, hit_sound: u8) -> dossier_audio::Track {
        let map = slider_map(hit_sound);
        let state = GameState::new(&map, &held_replay());
        let pack = dir.map_or_else(dossier_audio::SamplePack::default, |d| {
            dossier_audio::SamplePack::load(d)
        });
        build(
            &state,
            &map,
            |map_ms| map_ms / 1000.0,
            4.0,
            dossier_audio::Kit::plain(),
            pack,
        )
    }

    #[test]
    fn a_slider_being_held_sounds_for_as_long_as_it_lasts() {
        // The whole point of `sustain`: a `sliderslide` is a fifth of a second
        // of recording and the slider runs for half of one, so the sound has
        // to be looped rather than stamped.
        let dir = samples_with(&["normal-sliderslide"]);
        let track = track_for(Some(&dir), 0);
        // Between the head's own hit and the tail's, where nothing is struck.
        assert!(loudness(&track, 1.2, 1.4) > 8, "the slide is not sounding");
    }

    #[test]
    fn a_skin_without_the_loop_leaves_the_slider_silent() {
        // Nothing is synthesised for a held sound, so a play whose only object
        // is a slider makes noise at its two ends and nowhere between.
        let track = track_for(None, 0);
        assert_eq!(loudness(&track, 1.2, 1.4), 0, "something was invented");
    }

    #[test]
    fn the_whistle_is_held_alongside_the_slide_rather_than_instead_of_it() {
        // ```csharp
        // if (normalSample != null) slidingSamples.Add(normalSample.With("sliderslide"));
        // if (whistleSample != null) slidingSamples.Add(whistleSample.With("sliderwhistle"));
        // ```
        //
        // Two `if`s, not an `else`. A slider with a whistle holds both.
        let dir = samples_with(&["normal-sliderslide", "normal-sliderwhistle"]);
        let plain = loudness(&track_for(Some(&dir), 0), 1.2, 1.4);
        let whistled = loudness(&track_for(Some(&dir), sound_bits::WHISTLE), 1.2, 1.4);
        assert!(plain > 8, "the slide alone is sounding: {plain}");
        assert!(
            whistled > plain,
            "the whistle replaced the slide instead of joining it: {whistled} against {plain}"
        );
    }

    #[test]
    fn a_note_that_names_its_own_sound_file_is_still_given_the_skins() {
        // stable's `Use skin's sound samples`, which ships enabled there and is
        // not a setting here: "always use the selected skin's hitsounds instead
        // of the beatmap's included hitsounds".
        //
        // The fifth field of `hitSample` names a `.wav` inside the beatmap's
        // own folder. Asked as "does naming one change anything" rather than
        // "does something sound": a note that names a file and one that does
        // not are the same note to this engine, and nothing but a beatmap-file
        // lookup could make them differ.
        let dir = samples_with(&["normal-hitnormal"]);
        let sounded = |sample: &str| {
            let map = Beatmap::parse(&format!(
                "osu file format v14\n\n[Difficulty]\nCircleSize:4\nApproachRate:5\n\n\
                 [TimingPoints]\n0,500,4,1,0,100,1,0\n\n\
                 [HitObjects]\n100,192,1000,1,0{sample}\n"
            ))
            .expect("test map should parse");
            let state = GameState::new(&map, &held_replay());
            let track = build(
                &state,
                &map,
                |map_ms| map_ms / 1000.0,
                4.0,
                dossier_audio::Kit::plain(),
                dossier_audio::SamplePack::load(&dir),
            );
            loudness(&track, 1.0, 1.2)
        };

        let plain = sounded("");
        assert!(plain > 8, "the skin's own hit is not sounding at all: {plain}");
        assert_eq!(
            sounded(",0:0:0:0:map-clap.wav"),
            plain,
            "the note's own file changed what was played"
        );
    }

    #[test]
    fn a_sound_the_skin_blanked_is_silent_rather_than_invented() {
        // A file that is there but holds nothing is how a skin removes an
        // element, and it is not the same as a file that is not there.
        //
        // ```csharp
        // byte[] data = store.Get(name);
        // factory = factories[name] = data == null ? null : new SampleBassFactory(data, …);
        // ```
        //
        // osu! takes the first result that is not null, and a blank file is
        // `byte[0]` rather than null — so the blank wins and nothing is heard.
        // Read as an absence instead, the lookup would fall through to another
        // bank and, failing that, synthesise the very sound somebody removed.
        let map = Beatmap::parse(
            "osu file format v14\n\n[Difficulty]\nCircleSize:4\nApproachRate:5\n\n\
             [TimingPoints]\n0,500,4,1,0,100,1,0\n\n\
             [HitObjects]\n100,192,1000,1,0\n",
        )
        .expect("test map should parse");
        let state = GameState::new(&map, &held_replay());
        let struck = |dir: &std::path::Path| {
            let track = build(
                &state,
                &map,
                |map_ms| map_ms / 1000.0,
                4.0,
                dossier_audio::Kit::plain(),
                dossier_audio::SamplePack::load(dir),
            );
            loudness(&track, 1.0, 1.2)
        };

        // Nothing there at all: the engine has no sound to play and makes one,
        // which is what a skin that simply left the file out should get.
        let missing = samples_with(&["nothing"]);
        assert!(struck(&missing) > 8, "a missing sound was not synthesised");

        // And the same folder with the file present and empty.
        let blanked = samples_with(&["blanked"]);
        std::fs::write(blanked.join("normal-hitnormal.wav"), []).expect("a blank");
        assert_eq!(struck(&blanked), 0, "something was invented");
    }

    #[test]
    fn a_slider_nobody_played_is_silent() {
        // The same rule the struck sounds follow: the track is built from the
        // judgement, not from the map, and a missed object makes no noise. The
        // silence is the information.
        let dir = samples_with(&["normal-sliderslide"]);
        let map = slider_map(0);
        let state = GameState::new(&map, &replay(vec![
            ReplayFrame { time_ms: 900, x: 0.0, y: 0.0, keys: Keys(0) },
            ReplayFrame { time_ms: 2000, x: 0.0, y: 0.0, keys: Keys(0) },
        ]));
        let track = build(
            &state,
            &map,
            |map_ms| map_ms / 1000.0,
            4.0,
            dossier_audio::Kit::plain(),
            dossier_audio::SamplePack::load(&dir),
        );
        assert_eq!(loudness(&track, 1.2, 1.4), 0);
    }
}
