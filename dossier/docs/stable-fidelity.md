# Where this engine stands against stable

osu!stable is closed source. There is no repository to read, so "what stable
does" has to come from reimplementations that set out to match it. Two are
used here, and they are independent of each other:

- **danser-go** (`app/rulesets/osu/`), GPL-3.0 — a deliberate reimplementation
  of stable's gameplay core, with a separate lazer code path alongside it.
- **osu!lazer's Classic mod** (`OsuModClassic`, `LegacyHitPolicy`,
  `OsuHitWindows`), MIT — lazer restoring stable behaviours it otherwise
  departs from. Its comments state which stable behaviour each piece is for.
- **kionell/osu-standard-stable**, MIT — a TypeScript port of the standard
  ruleset aimed at stable. It carries the object model, the hit windows and the
  stacking, and **no replay judgement at all**: no note lock, no click handling,
  nothing about what a press does. So it votes on rules, not on the open
  question.

Where the two agree, the answer is as settled as it can be without the source.
Every row below was read out of one or both rather than reasoned about.

## Settled, and we match

| Rule | Stable | Here |
|---|---|---|
| Window sizes | `difficulty_range(80,50,20)`, `(140,100,60)`, `(200,150,100)` | same |
| Window rounding | `Math.Floor(...)` — truncated, not rounded | `.trunc()` |
| Window comparison | `<= floor(w) - 0.5`, i.e. `< w` for whole-millisecond offsets | exclusive `<` |
| Hittable range | `MISS_WINDOW = 400` | `HITTABLE_RANGE_MS = 400` |
| Click on a note, beyond 400ms | `ResultFor` gives `None` → `ClickAction.Shake`; nothing consumed | shake recorded, nothing consumed |
| Click on a note, within 400ms but outside the 50 window | `ResultFor` gives `Miss` → judged, and the note is consumed | same |
| Click that misses the circle | not `inRange`: no hit, no miss, no shake | same |
| Slider verdict | proportion of parts collected; head accuracy not required | same |
| Follow circle | plain radius to start a slide, 2.4x only while sliding | same |
| Slider tail | `max(start + duration/2, end - 36)` | same |
| Tail drops combo | no — a dropped tail costs the 300 and nothing else | same |

Two of those were fixed here *before* being confirmed, from the corpus alone:
the truncation and the exclusive comparison. Finding them stated outright in
`OsuHitWindows.SetDifficulty` is the strongest evidence available that the
corpus method works.

The windows now have a third vote. `StandardHitWindows.ts` lists the same three
ranges and adds `Miss` at a flat 400 with `isHitResultAllowed(Miss)` true —
which is exactly the line a truncated grep once hid from me, and which decides
whether a click outside the 50 window consumes the note or merely shakes it.
Three independent ports agreeing on it closes the question.

Stacking matches too, constant for constant: a stack distance of 3 osu!pixels,
a threshold of `preempt * stackLeniency`, and the check made against both an
object's start position and, for sliders, its end position.

## Not settled by a reference: the spinner

`Spinner.ts` computes `spinsRequired = trunc(seconds * 0.6 * range(OD, 3, 5,
7.5))` — and disclaims itself in the same file:

> Spinning doesn't match 1:1 with stable, so let's fudge them easier for the
> time being.

The constant is literally named `STABLE_MATCHING_FUDGE`. So this is not a third
vote; it is lazer saying it does not know either.

This engine uses `(100 + 15 * OD)` rotations per minute, derived from the corpus
rather than from a source: spinner misses across every replay went to zero when
it landed, having sat at a steady 70-72% of the requirement before. The two
formulas differ by 3-8% depending on OD, and swapping ours for lazer's leaves
the corpus at 16 exact and 816 error, identical to the digit — every player in
it spins far enough clear of both thresholds for the difference to decide
nothing.

No evidence either way, then. Ours stays, because it came from measurement and
the alternative is disclaimed by its own author.

## Known differences

`judge_heads` now offers each press to the object under the cursor and consults
the lock about that object, which is the shape both references use. Two of the
three differences below closed with it; the third is still open.

### Closed: the lock's own tolerance

`LegacyHitPolicy.CheckHittable` blocks only when an earlier unjudged object
**ended at least 3ms before** the tested one starts:

```csharp
if (testObject.HitObject.GetEndTime() + 3 < hitObject.HitObject.StartTime)
    return ClickAction.Shake;
```

Implemented. It changes nothing on this corpus, and the reason is worth
recording: on a map whose objects do not overlap in time, every earlier object
ended before the next one started, so the tolerance never decides anything. It
only speaks on 2B patterns, of which the corpus has none.

### Closed: the stack exemption

```csharp
if (previousHitObject.HitObject.StackHeight > 0 && !previousHitObject.AllJudged)
    return ClickAction.Ignore;
```

Implemented; stack heights are kept on the object rather than discarded after
the shift. `Ignore` is neither a hit nor a shake — the click passes through
untouched. On the stream trainer it catches four presses that were previously
refused. No change to any total, because a refused press and an ignored one
both come to nothing; the difference is that one rattles the pile and the other
does not.

### Open: a hit slider head keeps blocking beneath itself

```csharp
slider.HitArea.CanBeHit = () => !slider.DrawableSlider.AllJudged;
```

Not modelled.

## What the restructure found

Nothing moved on the corpus: 16 exact and 816 total error before and after, with
no replay changing by a single verdict. That is a real result rather than a
failed one — it says the two structures agree wherever objects do not overlap,
which is everywhere in ordinary mapping.

Instrumenting the stream trainer says where its 43 extra misses actually come
from. Of 404 presses:

| | |
|---|---|
| landed | 307 |
| **refused by the lock** | **69** |
| found no object under the cursor | 24 |
| ignored, stacked predecessor | 4 |
| eaten as an early click | 0 |
| beyond the hittable range | 0 |

So the cascade is the lock, and it is the lock firing *where stable's fires
too* — the rule is the same one, applied at the same moments. The remaining
error is therefore not a missing exception in `CheckHittable`.

## When a note stops being live — settled

The question this left open was when each side gives up on a note. danser
retires it at `Hit50`; lazer's `HitWindows.CanBeHit` allows a hit out to
`MISS_WINDOW`, 400ms. Both cannot be stable, and this engine followed danser
without having checked.

Measured. Retiring at 400ms takes the corpus from 16 exact and 816 total error
to **8 exact and 4810**, and every replay in it gets worse — not one is
unaffected, and the worst goes from 50 to 1644. The margin is far too wide to
be a tuning question.

So danser is right about stable here and lazer genuinely differs, which is
consistent with `OsuModClassic` not touching it: the Classic mod restores the
behaviours ppy chose to restore, and it is not a promise that everything else
matches. Two references agreeing is strong evidence; one reference alone is a
hypothesis, and this one was worth an hour to disprove.

`past_it` stays at the 50 window.


## What the trace found at 29.3s, and the fix that did not work

`--trace` put three timestamps on the stream trainer's cascade. Opening the
biggest one, 13 refusals in a row at 29.3s, gives the whole mechanism.

The map there is jumps: circles every 150ms, scattered, radius 45.4 under EZ.
The replay, frame by frame around the first refusal:

```
  29136  (229.0, 144.0)  keys 0   48.8px from the note at 29152
  29141  (227.9, 145.1)  keys 2   47.2px      ← the click
  29159  (223.0, 149.2)  keys 2   40.9px      ← inside, but no new press
```

The player pressed 11ms early and **1.8 pixels outside** the circle, held the
button, and the cursor arrived on the note 7ms after it was due. Our rule takes
only the rising edge, so the note is never hit. It then sits unjudged for its
165ms window, and the lock — correctly, by stable's own rule — refuses every
click that follows. One click 1.8px off costs 13.

So the cascade is not a note lock problem. It is a *click* problem, and the
lock only propagates it.

### The obvious fix, measured and rejected

A recorded position is a sample, not the truth: replays store about sixty a
second while the play that made them ran far faster. danser's structure does
carry a press past its own frame — the button's edge stays raised until the
next replay frame replaces it, and the ruleset runs several times in between
against an interpolated cursor. Letting a press reach an object over the
following milliseconds should therefore recover exactly these.

Measured over the whole corpus, from a 2ms carry to the full inter-frame gap:

| carry | exact | total error | replays better | worse |
|---|---|---|---|---|
| none | 16 | 816 | — | — |
| 2ms | 16 | 712 | 1 | 3 |
| 3ms | 17 | 712 | | |
| 4ms | 16 | 734 | | |
| 5ms | 16 | 740 | | |
| 6ms | 16 | 720 | 3 | 5 |
| to the next frame | 14 | 796 | 3 | 8 |

Every carry beats no carry on the total, and every one of them hurts more
replays than it helps. The aggregate improvement is almost entirely one replay
(202 to 60), which is the same shape as the four note-lock relaxations already
measured and rejected: fixes the pathological case, costs the ordinary ones.

The curve is also not monotonic — 3ms and 6ms both beat 4ms and 5ms — which
means individual replays crossing boundaries rather than a real optimum. And
the value that would fix the case diagnosed above is about 6ms, while the value
that scores best is 2 to 3ms. A rule whose best setting does not fix the
observation that motivated it is not the rule.

Reverted. What is kept is the diagnosis, which is exact, and the knowledge that
this particular door is closed.

## Where the remaining error actually lives

Removing the note lock entirely, in the current structure, is the sharpest
measurement taken so far:

| | with the lock | without |
|---|---|---|
| exact matches | 16 | **18** |
| total error | 816 | 1342 |

The replays that change tell the story:

| replay | with | without |
|---|---|---|
| Camellia — Stream Training | 448 | **0** |
| Camellia — Stream Training | 202 | **0** |
| tokken [AR10] | 86 | 6 |
| Chambarising (stream practice) | 50 | **1246** |

On the jump and stream trainers the lock is not merely unhelpful — it is the
entire error. tokken without it lands 354 clicks, which is the number osu!
itself reports, to the click. Two Camellia replays go to exact. Meanwhile one
mashed replay explodes from 50 to 1246.

So the lock as modelled here is wrong on maps played roughly one click per
note, and load-bearing on a map where the player mashes. That is a much sharper
statement than "note lock is the weak spot", and it says what to look for: not a
looser lock or a stricter one, but the condition stable uses that distinguishes
the two — something that releases for a player who is on the notes and holds for
one who is not.

Four relaxations of the lock have already been measured and lost against the
old structure; this says they were the wrong shape rather than the wrong idea.
The next attempt should be aimed at that condition, and it now has a target to
hit: three replays that must reach zero and one that must not move.

## The hunt for the releasing condition

Four candidates, each with its own rationale, each measured over the corpus:

| condition | exact | error | better | worse |
|---|---|---|---|---|
| the lock as it stands | 16 | 816 | — | — |
| a note the player visibly went for stops blocking | 16 | 1294 | 1 | 4 |
| only a note under the cursor blocks | 18 | 1342 | 3 | 6 |
| a later click writes off the notes stuck behind it | 18 | 1374 | 3 | 5 |
| only a note whose time has not come blocks | 18 | 1346 | 3 | 5 |
| no lock at all | 18 | 1342 | 3 | 6 |

Three of them land on the *same partition*: the three trainers go to 0, 0 and
4-6 error, one replay goes from 50 to about 1240, and four good replays lose a
few. They are the same rule wearing different clothes. The one that behaves
differently — "went for it" — only differs because a mashing player marks
everything as attempted.

### Why the trainers cascade at all

Measured on the Camellia stream, which is the cleanest failure: osu! scores it
78.5% with **9 misses** and we produce **232**, refusing 224 of 343 presses.
Combo agrees exactly at 64, so the first 64 seconds judge correctly and then it
collapses and never recovers.

The geometry says why. Circle radius 36.5px, consecutive notes 38px apart — so
only one note is ever under the cursor. The 50 window is 135ms against a stream
step of 83ms, so a note's window covers the next 1.6 notes. One unhit note
therefore blocks the next one or two, which go unhit and block the ones after
them. With this lock, a single miss in a stream whose window exceeds its
spacing cascades to the end of the map, by construction.

Stable cannot behave that way; every stream player would find the game
unplayable after one miss.

### What is left

One replay resists every candidate: a 37%-accuracy run over 2229 objects, where
osu! reports 843 misses and the lock as it stands gets within 50. Release the
lock in any of the four ways and it goes to ~1250, gaining about 600 300s it
should not have.

The open question is whether the lock is doing real work there or is being
accidentally right. It suppresses roughly as many clicks as the player genuinely
missed, which would be a coincidence worth checking: the next measurement is
object by object — do the refusals on that replay line up with the notes osu!
actually scored as misses, or merely add up to the same number? That answer
decides whether the lock keeps its place or the corpus needs a replay that can
tell these two apart.