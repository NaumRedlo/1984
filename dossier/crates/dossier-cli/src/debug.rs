//! `debug` — the judgement read back as a narrative.
//!
//! Every other output here is a summary: totals, counts, a histogram. Those say
//! *that* a play was judged wrongly. This says what the engine was looking at,
//! object by object and click by click, over a window small enough to read.
//!
//! It exists because the remaining error is a note lock cascade, and a cascade
//! cannot be read from totals. Twenty-seven refusals in a row are twenty-seven
//! symptoms of one unjudged note, and the only question worth asking is which
//! note and why nobody took it. So a refusal names its blocker, and the blocker
//! gets a section of its own listing every click that came near it.

use dossier_beatmap::Beatmap;
use dossier_replay::Replay;
use dossier_sim::{GameState, Judgement, Part, PressDetail, TimedObject, Verdict};

/// Clicks this far either side of an object are about that object.
const NEARBY_MS: f64 = 400.0;

pub fn narrate(
    replay_path: &str,
    map_source: &str,
    beatmap: &Beatmap,
    replay: &Replay,
    state: &GameState,
    window: (f64, f64),
) -> String {
    let (from, to) = window;
    let difficulty = state.difficulty();
    let mut out = format!("── {replay_path}\n");
    out.push_str(&format!(
        "   map     {} - {} [{}]\n",
        beatmap.metadata.artist, beatmap.metadata.title, beatmap.metadata.version
    ));
    out.push_str(&format!("   file    {map_source}\n"));
    out.push_str(&format!(
        "   player  {}   mods {}\n",
        replay.player, replay.mods
    ));

    // The four numbers, as the engine has them after mods — stated rather than
    // assumed, because a wrong one here looks exactly like a wrong lock.
    out.push_str(&format!(
        "\n   OD {:.2} → windows {:.0} / {:.0} / {:.0}      CS {:.2} → radius {:.2}, follow {:.2}\n",
        difficulty.overall_difficulty,
        difficulty.hit_window_300(),
        difficulty.hit_window_100(),
        difficulty.hit_window_50(),
        difficulty.circle_size,
        difficulty.circle_radius(),
        difficulty.circle_radius() * dossier_sim::judge::FOLLOW_CIRCLE_SCALE,
    ));
    out.push_str(&format!(
        "   AR {:.2} → preempt {:.0}, fade {:.0}          HP {:.2} → not modelled; it decides\n   \
         when a player dies, not what they hit\n",
        difficulty.approach_rate,
        difficulty.preempt_ms(),
        difficulty.fade_in_ms(),
        difficulty.hp_drain,
    ));

    out.push_str(&format!("\n   {from:.0}ms … {to:.0}ms\n\n"));
    out.push_str(&timeline_lines(state, window));
    out.push_str(&stuck_notes(state, window));
    out
}

/// Objects and presses interleaved, in the order the engine met them.
fn timeline_lines(state: &GameState, (from, to): (f64, f64)) -> String {
    let objects = &state.timeline().objects;
    let mut lines: Vec<(f64, u8, String)> = Vec::new();

    for (index, object) in objects.iter().enumerate() {
        if object.start_ms < from || object.start_ms > to {
            continue;
        }
        // 0 sorts objects before presses at the same instant: the note is
        // there to be clicked before the click happens.
        lines.push((object.start_ms, 0, object_line(state, index, object)));
    }
    for press in state
        .press_detail()
        .iter()
        .filter(|p| p.time_ms >= from && p.time_ms <= to)
    {
        lines.push((press.time_ms, 1, press_line(state, press)));
    }
    lines.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));

    if lines.is_empty() {
        return "   nothing in this window\n".to_owned();
    }
    lines.into_iter().map(|(_, _, text)| text).collect()
}

fn object_line(state: &GameState, index: usize, object: &TimedObject) -> String {
    let kind = if object.is_spinner() {
        "spinner"
    } else if object.is_slider() {
        "slider "
    } else {
        "circle "
    };
    let verdict = state
        .judge()
        .and_then(|judge| {
            judge
                .events_for(index)
                .find(|e| e.part.counts_for_accuracy())
                .map(|e| e.result)
        })
        .map_or("—".to_owned(), |result| format!("{result:?}"));
    let head = state.judge().and_then(|judge| {
        judge
            .events_for(index)
            .find(|e| e.part == Part::SliderHead)
            .map(|e| e.result)
    });
    let head_note = match head {
        Some(Judgement::Miss) => "  head lost".to_owned(),
        _ => String::new(),
    };
    let stack = match object.stack_height {
        0 => String::new(),
        height => format!("  stacked {height}"),
    };
    format!(
        "   {:>8.0}  #{index} {kind} at ({:.0},{:.0}) — {verdict}{head_note}{stack}\n",
        object.start_ms, object.pos.x, object.pos.y
    )
}

fn press_line(state: &GameState, press: &PressDetail) -> String {
    let objects = &state.timeline().objects;
    let where_it_went = match (press.object_index, press.error_ms, press.distance_px) {
        (Some(index), Some(error), Some(distance)) => format!(
            "#{index} — {error:+.0}ms, {distance:.2}px of {:.2}",
            press.radius_px
        ),
        _ => "nothing under the cursor".to_owned(),
    };
    let blocker = match press.blocked_by {
        Some(index) => {
            let at = objects.get(index).map_or(0.0, |o| o.start_ms);
            format!("  ← blocked by #{index}, due {at:.0}ms and still unjudged")
        }
        None => String::new(),
    };
    format!(
        "   {:>8.0}    press  {:<20} {where_it_went}{blocker}\n",
        press.time_ms,
        press.verdict.name()
    )
}

/// The notes the lock is stuck on, and every click that came near them.
///
/// A run of refusals all name the same object; the question is never what the
/// refusals did, it is why that one note was never judged. So it gets the
/// clicks that were within reach of it, with what each was doing instead.
fn stuck_notes(state: &GameState, (from, to): (f64, f64)) -> String {
    let detail = state.press_detail();
    let mut blockers: Vec<usize> = detail
        .iter()
        .filter(|p| p.time_ms >= from && p.time_ms <= to)
        .filter_map(|p| p.blocked_by)
        .collect();
    blockers.sort_unstable();
    blockers.dedup();
    if blockers.is_empty() {
        return String::new();
    }

    let objects = &state.timeline().objects;
    let mut out = String::new();
    for blocker in blockers {
        let Some(object) = objects.get(blocker) else {
            continue;
        };
        let refusals = detail
            .iter()
            .filter(|p| p.blocked_by == Some(blocker))
            .count();
        out.push_str(&format!(
            "\n   the lock is stuck on #{blocker} at {:.0}ms ({:.0},{:.0}) — it refused {refusals} click(s).\n   \
             Every press within {NEARBY_MS:.0}ms of it:\n",
            object.start_ms, object.pos.x, object.pos.y
        ));
        let mut near = 0;
        for press in detail
            .iter()
            .filter(|p| (p.time_ms - object.start_ms).abs() <= NEARBY_MS)
        {
            let distance = press
                .distance_px
                .map(|_| press_distance(state, press, blocker));
            let reach = match distance {
                Some(d) if d <= press.radius_px => "on it",
                Some(_) => "off it",
                None => "—",
            };
            out.push_str(&format!(
                "      {:>8.0}  {:+5.0}ms  {:>7}  {reach:<6} → {}\n",
                press.time_ms,
                press.time_ms - object.start_ms,
                distance.map_or("—".to_owned(), |d| format!("{d:.2}px")),
                describe(press.verdict),
            ));
            near += 1;
        }
        if near == 0 {
            out.push_str("      none — nothing came near it, so the miss is the player's\n");
        }
    }
    out
}

/// How far a press was from *this* object, rather than from the one it was
/// tested against — which is the number that says whether it could have taken
/// the blocker instead.
fn press_distance(state: &GameState, press: &PressDetail, object_index: usize) -> f64 {
    let cursor = state.cursor_track().sample(press.time_ms);
    let object = &state.timeline().objects[object_index];
    cursor.map_or(f64::INFINITY, |c| c.pos.distance_to(object.pos))
}

fn describe(verdict: Verdict) -> String {
    match verdict {
        Verdict::Refused {
            object, blocked_by, ..
        } => format!("refused on #{object}, blocked by #{blocked_by}"),
        Verdict::Landed { object } => format!("landed on #{object}"),
        Verdict::TookItEarly { object } => format!("took #{object} early"),
        Verdict::OutOfRange { object } => format!("out of range of #{object}"),
        Verdict::Ignored { object } => format!("ignored, stacked before #{object}"),
        Verdict::FoundNothing => "found nothing".to_owned(),
    }
}
