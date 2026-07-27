# Where this engine stands against stable

osu!stable is closed source. There is no repository to read, so "what stable
does" has to come from reimplementations that set out to match it. Two are
used here, and they are independent of each other:

- **danser-go** (`app/rulesets/osu/`), GPL-3.0 — a deliberate reimplementation
  of stable's gameplay core, with a separate lazer code path alongside it.
- **osu!lazer's Classic mod** (`OsuModClassic`, `LegacyHitPolicy`,
  `OsuHitWindows`), MIT — lazer restoring stable behaviours it otherwise
  departs from. Its comments state which stable behaviour each piece is for.

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
