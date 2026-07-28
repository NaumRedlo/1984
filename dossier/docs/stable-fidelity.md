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

### Closed: a slider keeps blocking beneath itself

```csharp
slider.HitArea.CanBeHit = () => !slider.DrawableSlider.AllJudged;
```

A slider is judged as a whole at its end, so its head keeps a live hit area for
the length of the slide. A note underneath it never sees the click: the head
swallows it and, being judged already, does nothing with it.

Implemented, and it needed a second cursor into the object list. The existing
one steps over anything judged, and a slider counts as judged the moment its
head is taken — while it is still on the playfield for another half second. The
scan for what is still *playing* has to start further back than the scan for
what is still *unjudged*.

Nothing moved on the corpus, and nothing could: only a 2B map puts a note under
a travelling slider. It is implemented so that a 2B map is not judged by
accident, the same reason the lock's 3ms slack is in.

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

## A play that stopped early

If a player fails, osu! stops judging where they died while this engine judges
the whole map and buries the difference in misses. That would produce exactly
the shape seen on the trainers — a run of correct verdicts and then a wall of
invented misses — so it was worth checking rather than assuming.

It is not what happens on the trainers. Every replay in the original corpus
accounts for every object: the header's four counts sum to the map's object
count in all 27, with no exceptions, and Camellia's recording runs to 86.8s
against a last note at 85.8s. Nobody failed, nobody quit, and none of those
comparisons is against a partial play.

It does happen, though. Two failed runs of DragonForce - My Heart Will Go On
[SinHay's Extra] arrived afterwards and read 869 and 863 misses adrift, because
the map is 1127 objects long and the plays reached 258 and 250 of them.

The header says how far a play got without being asked: its four counts name
one object each, so their sum *is* the number of objects judged. Both sides are
now counted over that many objects and the rest of the map is left out of the
comparison — which leaves a real question rather than a ruined one: the same
objects, and whether we judged them as osu! did. On the first of those two
replays the answer is all 258, combo included.

One detail worth keeping: on a failed play the frames stop before the judging
does. stable records a frame only when the input changes, and a player who has
given up stops moving — the first of these two replays ends its recording at
77.1s while osu! went on judging to 78.3s, where the health bar finally emptied.
Cutting the play at the last frame would have been wrong by thirteen objects.

The object count gets the moment right to the millisecond, and the header can
be made to say so. Counting forward 258 objects lands on a circle at 78276ms;
nobody hit it, so its verdict falls when its fifty window shuts at OD 9.3 —
78276 + 107 = **78383ms**. The last sample in the replay's own life-bar graph
is `78383|0`. Two independently derived numbers, one written by osu! and one
computed here from an object count and a hit window, agreeing exactly.

So the play is cut there in full: `verify` compares over the objects it
reached, and the render stops at that instant with the HUD holding the score
the report verified. Past it the map would go on with no player in it — on this
replay for another two minutes.

## Object by object: the lock is right where it matters

The header carries totals, not per-object verdicts, so there is no way to ask
directly which notes osu! missed. Maximum combo is the next best thing and it is
a *positional* fingerprint: it depends on where the misses fall, not how many
there are.

| replay | combo, with the lock | without |
|---|---|---|
| Chambarising | **422 / 422** | 424 / 422 |
| Camellia #1 | 64 / 64 | 66 / 64 |
| Camellia #2 | 168 / 168 | 169 / 168 |
| tokken | 50 / 50 | 51 / 50 |
| Tsukiyura | 85 / 85 | 85 / 85 |

With the lock all five agree. Without it, three break.

For Chambarising that settles the question this was asked to settle. A run of
422 requires 422 consecutive objects judged exactly as osu! judged them, on a
map of 2229 with 843 misses in it. That is not something a wrongly-tuned rule
produces by accident, and the four buckets agree to within 4% besides:
624/610/175/820 against 609/600/177/843.

**The lock is right there on the merits.** So all four releasing conditions were
wrong in kind rather than in degree — they broke something that works. The
target has moved: the trainers' first wrong verdict is what to find, exactly as
tokken's turned out to be a click 1.8px off a circle. The cascade is only ever
the amplifier.
## When a missed slider head becomes a miss

A slider head is judged like a circle: unhit when its fifty window shuts, it is
a miss and the combo breaks. On a short slider that window shuts *after* the
slider itself has ended — 200bpm quarter-note sliders are 75ms against a 107ms
window at OD 9.3 — so the order of events is: the slider's end lands its combo,
and only then does the head's break arrive.

This engine used to clamp the miss to the slider's end, which reversed those
two and cost one combo every time a short slider's head went unhit. Removing
the clamp is worth three replays:

| replay | combo, clamped | unclamped | osu! |
|---|---|---|---|
| DragonForce (failed run) | 111 | **112** | 112 |
| Shinteki Souzou | 120 | **121** | 121 |
| NIVIRO - Memes | 289 | **290** | 290 |
| Unsafe Speeds | **372** | 371 | 372 |

Unsafe Speeds is the one that argues for the clamp, and it is the one that
cannot: its counts already disagree by a miss, so osu! broke somewhere we do
not and its 372 is not a run we can reconstruct. The two replays that decide it
cleanly are the other way — DragonForce matches osu! on all four counts, and
Shinteki's only disagreement is a 300 against a 100, which cannot move a combo
either way.

Corpus: 16 exact to 17, total error 1322 to 1320. (The corpus is 29 replays
from here on — the two DragonForce runs joined it — so these totals are not the
same scale as the 27-replay figures quoted earlier in this file.)

The clamp was there because a miss that lands past its own object looks wrong.
It is not: the head's window is the head's, and stable does not shorten it to
fit the slider. Attribution is unaffected — which object a click may hit is
decided in `judge_heads`, and this is only the moment the verdict is filed.

## Reading a window of clicks

`judge --trace` totals every press by what became of it; `--trace --from --to`
now lists them one by one, with the object each was tested against, how late it
was and how far from the centre it landed. Those three numbers are what every
judgement question so far has come down to — tokken's was a press 1.8px outside
a 45.4px circle, and the head-miss ordering above was found by reading five
clicks around 54.8s. Reconstructing them by hand is how the same instrumentation
got written and deleted twice.

## The four difficulty numbers, closed

CS, AR, OD and HP feed judgement — OD sets the windows, CS the circle and with
it the follow circle and the stack offset, AR the preempt and with it the
stacking threshold. A quiet error in any of them looks exactly like a bug in
the note lock: the totals go wrong and nothing says why. So they were audited
rather than trusted, and are now pinned in `crates/dossier-beatmap/tests/difficulty.rs`.

**OD.** The windows are `80 - 6·OD`, `140 - 8·OD`, `200 - 10·OD`, truncated.
Precision decides the answer more often than it should: read the OD as a
32-bit float first — as lazer does, its difficulty fields being floats — and
42 of the 1001 ODs from 0.00 to 10.00 come out a millisecond narrower. Two of
the corpus replays sit on that fork, both at OD 9.3, and a failed play settles
it: its 258th object is a circle at 78276ms nobody hit, so osu! judged it a
miss when the window shut, and the health hit zero at that judgement — the
last life-bar sample is `78383|0`, and 78383 - 78276 = **107**, our value.
Computing entirely in 32-bit floats, as stable's C# would, agrees with our
64-bit decimal arithmetic on all 1001 values; only lazer's mixed path differs.

**CS.** `54.4 - 4.48·CS`, the form both danser and lazer use. The osu! wiki
quotes `4.4813` instead; the difference is 0.006 osu!pixels at CS 4, and the
corpus cannot tell them apart in principle — it *does* move by three clicks,
because near a circle's rim clicks are dense (about twenty per pixel), and one
of them lands at 35.6px against a 35.6px radius. Which way those three fall is
luck, not evidence. Ours stays: two reimplementations against one wiki page.

**AR.** Preempt is `difficulty_range(AR, 1800, 1200, 450)`, extrapolated past
AR 10 rather than clamped. The fade-in was **wrong**: it was `preempt * 0.66`
and is `preempt * 2/3` exactly — osu!'s own table gives 800ms at AR5 against a
1200ms preempt, 1200 at AR0 against 1800, 300 at AR10 against 450, and every
one of those is two thirds. An 8ms error at AR5, visual only. lazer computes it
as `400 * min(1, preempt / 450)`, a flat 400ms for every AR up to 10, which is
another place lazer is simply not stable.

**HP.** Parsed and used nowhere. It decides when a player dies, not what they
hit — and where the play ended is now read off the header's object count.

Mods: HardRock scales HP/OD/AR by 1.4 and CS by 1.3, capped at 10; Easy halves
all four; the vertical flip is applied before stacking, so distances and stack
heights are unchanged and the offsets still run up-left. Speed mods touch only
the clock.

## The debugger, and what it found

`dossier debug --from --to` reads a window back object by object and click by
click: the difficulty numbers in force, every press with the object it was
tested against, and — when the lock refuses — which note it is stuck on, plus
every click that came within 400ms of that note and what each did instead. A
refusal now names its blocker (`Verdict::Refused { object, blocked_by }`),
which is what makes a cascade readable backwards.

Pointed at the Camellia stream trainer it answered in one screen. The collapse
at 64.4s begins with a single click at 64346ms that lands inside **two**
overlapping circles — 34.1px into #63 and 19.2px into #64, against a 36.48px
radius. We give it to #63, the earlier one. #64 then goes unjudged, and every
following click is refused by it in turn: the player trails their own stream by
one note and the lock never lets them back in.

osu! gave that click to #64. The proof is in the combo, not the counts: with
the lock off our four counts match the header **exactly** on both Camellia
replays — 223/89/1/9 and 179/113/28/2 — and only the combo reads +2 and +1.
Counting the same number of hits while distributing them one note apart is
precisely what a one-note shift looks like, and the combo suspect names the
object: **#63, clicked at +40ms and 34.1px — 93% of the way to the rim**.

So the lock must be nearly silent on Camellia and is not. It must *not* be
silent on Chambarising: with the lock off that replay gains **623 invented
300s**, and with it on the totals land at 624/610/175/820 against 609/600/177/843
with the combo exact at 422. Both replays refuse clicks of the same shape —
Chambarising at 70.1s is a click 2.87px from the next note, refused by the
previous one, indistinguishable from Camellia's. Measured across both:

| | Chambarising (lock right) | Camellia (lock wrong) |
|---|---|---|
| refusals inside the 50 window | 98.9% | 100% |
| blocker one note back | 96.4% | 81.2% |
| median error of a refused click | +10ms | 0ms |

Neither axis separates them. Two more candidates died against the corpus:

- **Nearest circle instead of earliest**, when a click is inside several:
  catastrophic — Fleshgod goes from 2824/110/0/0 to 1913/359/120/542. On a
  clean play the cursor leads the click, so "nearest" eats the *next* note
  constantly.
- **Carrying the cursor forward** to where it is when the game processes the
  press rather than where the recording put it: 17 exact and 1320 error becomes
  13 and 2554 at a 2ms carry, and falls off a cliff from there. stable reads
  the position out of the pressing frame, as we do.

What is left is the question the debugger sharpened: a click inside two
overlapping circles, 93% of the way out of the earlier one and well inside the
later one, went to the later one — and no rule tried so far picks it without
wrecking the clean plays.

## Two notes in the same place

Stacking is the everyday version of the question the Camellia cascade asks —
what happens when a click covers more than one note — so the whole of it was
checked against stable case by case rather than assumed.

| Case | Stable | Here |
|---|---|---|
| Two circles on a point, within the leniency | stack, earlier lifted `height * scale * -6.4` | same: 3.2px up-left per step at CS 5 |
| Cursor covering the whole pile | judged in time order, front first | same |
| Cursor on the later note only, front unjudged | `ClickAction.Ignore` — the click vanishes | same |
| …once the front is judged | ordinary hit | same |
| Circle stacked on a slider's tail | stack runs the other way, down-right | same |
| Note under a slider still travelling | swallowed by the head's hit area | **was hit; now swallowed** |
| Two notes sharing an instant | no block — the lock needs the earlier one to have *ended* first | same |

The last row is the 3ms slack doing its job, and the one before it was the open
item above.

One test had to be thrown away to get here. `a_click_on_a_stacked_note_passes_through_untouched`
clicked the middle of the pile, where both circles overlap — so the click
landed on the front note by the ordinary rule and the exemption was never
consulted. It asserted only that nothing had been shaken, which is true of an
ordinary hit too, and it passed with the exemption deleted from the engine. The
replacement puts the cursor 31.1px from the later note and 35.6px from the
earlier one, where only one of them is reachable, and asserts the verdict
itself. That is the third hollow test found in this file; all three shared a
shape — asserting the *absence* of something rather than the presence of the
verdict that was supposed to happen.
