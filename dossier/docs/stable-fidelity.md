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

All three are notelock, which is this engine's documented weak point.

### 1. Notelock is stricter here

`LegacyHitPolicy.CheckHittable` walks the objects that are currently alive and
blocks only when an earlier unjudged one **ended at least 3ms before** the
object being tested starts:

```csharp
if (testObject.HitObject.GetEndTime() + 3 < hitObject.HitObject.StartTime)
    return ClickAction.Shake;
```

Here, any earlier unjudged object blocks unconditionally. On a map whose
objects do not overlap the two are the same rule. They part company on
overlapping patterns, where stable lets the click through and we do not.

### 2. The stack rule is missing

```csharp
if (previousHitObject.HitObject.StackHeight > 0 && !previousHitObject.AllJudged)
    return ClickAction.Ignore;
```

`Ignore` is neither a hit nor a shake: the click passes through untouched.
danser carries the same rule, commented "don't shake the stacks".

This cannot be expressed in the current structure at all. Both implementations
test *the object under the cursor* and look at its predecessor; ours offers each
press to the earliest unjudged object only, so that predecessor is judged by
construction and the rule can never fire. Stack heights are also computed in
`stacking::apply` and dropped rather than kept on the object.

### 3. A hit slider head keeps blocking beneath itself

```csharp
slider.HitArea.CanBeHit = () => !slider.DrawableSlider.AllJudged;
```

On stable a slider head that has already been hit goes on blocking input to
whatever sits underneath it until the whole slider is judged. Not modelled here.

## What this predicts

The remaining corpus failure is a stream trainer where three consecutive misses
cascade into 232, and all three differences above bear on exactly that: dense
stacked patterns, sliders overlapping the notes after them, and a lock that
releases later than stable's.

Fixing them means restructuring `judge_heads` so a press is offered to the
object under the cursor rather than to the earliest unjudged one, with the
lock consulted per object. That is a real change to the hot path of judgement
and has to be measured over the corpus like everything else — four relaxations
of the lock have already been tried and lost, and being able to name the exact
rule stable uses is not the same as having shown it helps here.
