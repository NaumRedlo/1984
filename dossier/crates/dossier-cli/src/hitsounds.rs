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
/// `from_ms` and `rate` are the same numbers the video uses, so a hit at map
/// time T lands at video time `(T - from) / rate` — under DoubleTime the sounds
/// compress along with everything else.
pub fn build(
    state: &GameState,
    beatmap: &Beatmap,
    from_ms: f64,
    rate: f64,
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
                    (event.time_ms - from_ms) / 1000.0 / rate,
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
        let (set, volume) = bank_for(beatmap, object, voice, edge);
        track.strike_with(
            voice,
            (event.time_ms - from_ms) / 1000.0 / rate,
            set,
            volume,
        );
    }
    track
}

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
        Part::Slider | Part::Spinner => None,
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
fn bank_for(
    beatmap: &Beatmap,
    object: &HitObject,
    voice: Voice,
    edge: Option<usize>,
) -> (SampleSet, f32) {
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

    (convert(set), volume)
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
        let (set, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
        assert_eq!(set, SampleSet::Soft);
        assert!((volume - 0.5).abs() < 1e-6);
    }

    #[test]
    fn a_notes_own_bank_overrules_the_timing_point() {
        // `3:0:0:0:` — drum for the plain hit, everything else inherited.
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,0,3:0:0:0:");
        let (set, _) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
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
        let (_, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
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
        let (set, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal, None);
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
            0.0,
            1.0,
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
            0.0,
            1.0,
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
        assert!(audible(&track), "losing it should be heard");
    }
}
