# Where this engine stands against stable and lazer

> The corpus holds replays from both clients and they do not judge alike. The
> rules are split in `crates/dossier-sim/src/ruleset.rs`, one variant each,
> with the source of every rule named where it is stated. Read that file first;
> this document is the evidence behind it.

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

## The corpus was never one client

The cascade that has resisted every rule for weeks was not a rule problem. It
was that eleven of the twenty-nine replays did not come from stable at all.

A replay's header carries the version of the client that wrote it. Everything
in the corpus reads `2023xxxx` through `20260711` — except eleven, which read
`30000016`, `30000017` and `30000018`. Those are lazer. And the three replays
that resisted hardest are all of them: both Camellia stream trainers at
`30000018`, and tokken at `30000017`.

The two clients do not judge the same way, and lazer's own source says so
plainly. `LegacyHitPolicy` — the Classic mod, stable's rules restored:

```csharp
public void HandleHit(DrawableHitObject hitObject)
{
}
```

Empty. Nothing is written off early; a note nobody reached waits for its own
window to shut. And the block is wide: any earlier unjudged object that *ended*
before this one started, with 3ms of slack.

`StartTimeOrderedHitPolicy` — lazer's own:

```csharp
if (!blockingObject.Judged && time < blockingObject.HitObject.StartTime)
    return ClickAction.Shake;
```

The block is far narrower — only a press that arrives *before* the blocking
note was even due — and `HandleHit` misses everything still unjudged behind the
note that was hit, there and then.

That is the whole of the Camellia cascade. The player trails their own cursor
by one note, so each click lands inside the next circle. Under lazer's rules
the stranded note is written off at the click and the run continues. Under
stable's it blocks, and every following click is refused: 9 real misses became
232.

Judged by the client that produced it, each replay lands where it should:

| | before | after |
|---|---|---|
| exact | 17 | **19** |
| total error | 1320 | **586** |

Both Camellia replays are now exact, counts and combo — 223/89/1/9 at 64, and
179/113/28/2 at 168. tokken went from 86 to 2. No stable replay moved by a
single verdict, because nothing about stable's path changed.

The lesson is worth stating plainly: for weeks the note lock was measured
against a corpus that silently contained two rulesets, and every candidate rule
was scored on its ability to satisfy both at once. Four looser locks were
rejected for failing on Chambarising while fixing Camellia — they were being
asked to be stable and lazer simultaneously, which no rule can be. `judge` now
prints the client on every report so this cannot happen quietly again.

### What this does not explain

Three replays still disagree, and all three are stable:

- **yax03 - down [H4CK3R]**, 356: combo 2335 against 2687 with the counts
  nearly right — a break we take and the game does not.
- **Kona-Chan**, 163: 48/5/2/0 against 55/0/0/0, combo 71 against 220. Every
  hit downgraded, which is a different failure entirely.
- **Chambarising**, 50: the mashed 37% run, 624/610/175/820 against
  609/600/177/843 with the combo exact at 422.

The lock is stable's own rule, and it stays. What is left is three specific
disagreements rather than one structural one.

## Splitting the ruleset

`ruleset.rs` now holds the two side by side, and the split is deliberate about
where each side's authority comes from.

**lazer is read straight out of `ppy/osu`.** There is nothing to infer: the
ruleset is the source, so each rule names its file and quotes it where it is
short enough. That is the easy half.

**stable is assembled.** It is closed source, so it comes from danser-go's
`app/rulesets/osu/`, from lazer's Classic mod — ppy restoring stable behaviours,
whose setting descriptions say which behaviour each one is — and from the
corpus itself. Stable replays carry their own totals, and a rule that disagrees
with them is wrong whatever its provenance. That is how the hit-window
truncation and the exclusive comparison were found here before either was
confirmed in a source.

Three rules differ so far, all of them established above:

| | stable | lazer |
|---|---|---|
| A click blocked by an earlier unjudged note | any that *ended* before this one started, +3ms slack | only a press arriving before that note was due |
| Landing a click | nothing written off; a stranded note waits for its window | everything unjudged behind it missed at once |
| A note under a travelling slider | swallowed by the head's live hit area | ordinary |

Deliberately shared: the object model, stacking, slider paths, timing, and the
shape of the `.osr` header. lazer exports legacy counts — its own judgements
converted back into 300/100/50/miss — which is worth checking rather than
assuming, and it holds: on all three lazer replays with a local map the four
counts sum to the map's object count exactly. So a slider stays one object with
one verdict on both sides, and the two halves are compared against the same
four numbers.

Inventing differences is as wrong as missing them, and the corpus is now scored
per client so that a change to one side cannot be paid for by the other:

```
  lazer     3 replays   exact   2   error      2
  stable   26 replays   exact  17   error    584
```

## Kona-Chan: a full combo read as 71

A stable replay, `HDHRDTFL`, 55 objects of which 52 are sliders: the player
finished it 55×300 and 220 combo — a perfect play. This engine read 48/5/2/0
and **71 combo**, dropping heads, repeats and tails all over the map.

The map is file format **v4**, and old maps break assumptions that never come
up on modern ones. Two of them, both about where the ball is:

### The authored length wins in *both* directions

A slider states its pixel length, and this engine trimmed the geometry to it —
correctly — but clamped when the geometry was *shorter*:

```rust
if target >= self.length {
    return;      // "the ball stops at the end of the drawn path"
}
```

That comment was wrong. osu! stretches the final segment instead:

```csharp
Vector2 dir = (calculatedPath[pathEndIndex] - calculatedPath[pathEndIndex - 1]).Normalized();
calculatedPath[pathEndIndex] = calculatedPath[pathEndIndex - 1] + dir * (float)(expectedDistance - cumulativeLength[^1]);
```

On this map it is not an edge case. `L|320:224|320:192` with an authored length
of 65 draws **32 osu!pixels**; stretched, it ends at (320, 159) — and the next
object sits at (320, 160). The map is telling us where the path ends. Leaving
the ball 33px short of it, on a CS 10 map whose follow circle is 23px, puts it
three follow circles from where the player is tracking.

### The modern stacking sweep does not belong on old maps

```csharp
if (beatmap.BeatmapVersion >= 6)
    applyStacking(beatmap, hitObjects, 0, hitObjects.Count - 1);
else
    applyStackingOld(beatmap, hitObjects);
```

Running the modern sweep on a v4 map piled one slider **eight steps high** and
moved the ball out from under a player who tracked it. Old maps are now left
flat, which is not what the game does either — but of the two answers available
it is the better one, and both were measured rather than assumed:

| | corpus error |
|---|---|
| modern sweep on old maps (before) | 526 |
| a port of `applyStackingOld` | 515 |
| leaving old maps flat | **465** |

The port was written and withdrawn. Scoring *worse than doing nothing* means it
is wrong somewhere, and shipping it would have hidden that behind the genuine
improvement sitting next to it. Old-map stacking stays an open item, honestly
labelled, rather than a plausible-looking wrong answer.

Kona-Chan went from 71 combo to 180, and the corpus from 586 to 465. What is
left on that replay is three parts lost by 0.3px, 2.9px and 0.3px against a
23.04px follow circle — a different question, and a much smaller one.

`sliders` now prints, for every dropped part, where the ball was and how far
the cursor was from it at that instant. The trail it printed before only ever
covered the run-in to the tail, which says nothing about a tick lost in the
middle of a 2.5-second slide.

## yax03 - down: one click, 352 combo

A stable replay with the counts almost right — 1967/22 against 1969/20, no
misses on either side — and the combo reading **2335 against 2687**. One break,
in a play the game recorded as unbroken.

The debugger named it in a single line: our only combo chain ended on the head
of slider #241, and the trace above it showed why.

```
63956    press  landed             #240 — -36ms, 12.83px of 36.48
63979    press  took a note early  #241 — -362ms, 34.75px of 36.48
63992  #240 slider  at (186,213) — Great
64341  #241 slider  at (152,212) — Ok  head lost
64344    press  found nothing      nothing under the cursor
```

The player alternates onto slider #240 — two presses, 63956 and 63979, the
second landing 13ms before the slider is even due. The cursor is 34.75px from
slider #241, which is 362ms away, so we handed it that: inside the 400ms
hittable range and outside the 50 window is an early miss that takes the note
with it. #241's head was gone before the player ever reached it, and their real
click at 64344 — three milliseconds off, five pixels from the centre — found
nothing left to hit.

### The wrong answer, and why it was tempting

The obvious reading is that 400ms is too wide for stable. Measuring the
threshold looks convincing:

| stable hittable range | corpus error |
|---|---|
| 400 (lazer's `MISS_WINDOW`) | 463 |
| 310–360 | **110** |
| 160–300 | 240 |
| 120 | 413 |

A clean optimum, a wide plateau, a 4× improvement. And it is wrong. The
plateau's edges are two individual clicks — one at −301ms on Unsafe Speeds
that *must* be eaten, one at −362ms here that must not — and any number between
them scores the same. Nothing about stable says 330. A constant fitted to two
clicks would have been a decoy nailed over the real bug.

### The right one

```csharp
slider.HitArea.CanBeHit = () => !slider.DrawableSlider.AllJudged;
```

The hit area is live for as long as the *object* is, and an object's life
starts when it spawns — not when it is due. This engine had the rule but tied
it to `start_ms <= press`, so a slider only swallowed clicks once it had begun.

Slider #240 spawned at 63497. At 63979 it is on the playfield, its head taken,
its body not yet judged — and the cursor is inside it. The click goes there and
stops. It never had anything to do with #241.

One condition, `start_ms - preempt`, and the same 110: the corpus optimum
reached by a rule with a source instead of a number with a curve. Both
constraining clicks are satisfied at the true 400, because neither was ever
about the threshold.

```
  lazer     3 replays   exact   2   error     2
  stable   26 replays   exact  17   error   110
```

from 586 at the start of the day.

## Chambarising: what it is not

The mashed 37% run, 2229 objects, 843 misses in the header. It reads
624/610/175/820 against 609/600/177/843 — twenty-three objects we credit that
the game did not — with the **combo exact at 422**. That last part matters: a
422-link run means 422 consecutive verdicts identical to osu!'s, so whatever is
wrong is not structural.

Six things were checked and none of them is it. Recording the dead ends is the
point — each one is a hypothesis that will otherwise be re-tried:

- **Sliders.** All 69 are credited correctly, 63×300 and 6×100, none missed.
  The entire disagreement is on circles.
- **Window edges.** The histogram either side of the 300 and 100 boundaries is
  smooth — 42ms:12, 43ms:11, 44ms:10, 45ms:8, 46ms:18. No off-by-one.
- **The circle's rim.** Twenty of 1404 landed clicks sit in the last pixel of
  the radius, forty in the last two. Fewer than the pixels inside them, which
  is what aiming at a centre looks like; nothing piled against the edge.
- **Double presses.** A frame where two keys go down at once would be one click
  to us and two to the game. There are none: every press on this replay sets
  either `M1+K1` or `M2+K2`, never both, and our 2243 equals the count by
  finger.
- **Strict frontmost.** Offering the click to the earliest unjudged object
  regardless of the cursor, rather than to the first one under it, scores
  identically — 110 either way. The corpus cannot tell those two apart, here or
  anywhere.
- **The lock's slack and shape.** Unchanged behaviour; the 3ms tolerance only
  speaks on 2B patterns, of which this map has none.

Two more went the same way afterwards:

- **Mashing.** A player hammering both keys would give us one press where the
  game counts two. There is no mashing here: one press in the whole replay
  follows the last by under 40ms, and 2033 of 2243 follow by more than 70. The
  player is alternating a 160bpm stream, not spamming it.
- **Holding notes alive longer.** Making an unhit note keep blocking past its
  fifty window is sharply worse in every direction: at 160ms the 300s fall from
  624 to 502 against the header's 609.

### What the search did settle

`judge --marginal <n>` ranks our hits by the room they had — the fraction of
the fifty window and the fraction of the radius, thinnest first. On this replay
almost every thin hit is thin *in space*: clicks sitting at 35.0 or 35.1 pixels
against a 35.14 radius, with plenty of time to spare.

That looked like the answer, and shrinking the radius does move the totals. It
moves the wrong ones. At every scale tried, the 100s and 50s come off and the
**300s stay at 624** against the header's 609 — while the rest of the corpus
collapses, from 20 exact to 7 at a 3% reduction.

So the fifteen 300s we owe are not marginal at all. They are clicks well inside
the circle and well inside the window: good presses, on the right note, at the
right time, that osu! did not credit. Nothing about geometry or timing can take
those away — only a rule that refuses a good click.

This engine has three such rules: the note lock, the slider swallowing what
lands on it, and the stack exemption. All three are implemented, and the stack
exemption never fires once on this replay. Whatever discards those fifteen
presses is a fourth thing, and it is not yet known.

That is a sharper question than the one this section started with, and it is
where Chambarising rests: 23 circles out of 2160, all of them outside the
422-link run, and a specific reason to think the cause is a missing rule rather
than a mistuned constant.

### danser has no fourth rule either

`CanBeHitStable` in `app/rulesets/osu/ruleset.go`, in full:

```go
func (set *OsuRuleSet) CanBeHitStable(time int64, object HitObject, player *difficultyPlayer) ClickAction {
	if _, ok := object.(*Circle); ok {
		index := -1
		for i, g := range set.processed {
			if g == object { index = i }
		}
		if index > 0 && set.processed[index-1].GetObject().GetStackIndexMod(player.diff) > 0 && !set.processed[index-1].IsHit(player) {
			return Ignored //don't shake the stacks
		}
	}
	for _, g := range set.processed {
		if !g.IsHit(player) {
			if g.GetNumber() != object.GetNumber() {
				if g.GetObject().GetEndTime()+Tolerance2B < object.GetObject().GetStartTime() {
					return Shake
				}
			} else { break }
		}
	}
	return Click
}
```

That is this engine, line for line: the stack exemption, then the first
unjudged earlier object whose end precedes this one's start by the 2B
tolerance. The caller adds the hittable range and nothing else:

```go
if math.Abs(float64(time-int64(object.GetObject().GetStartTime()))) >= hitRange {
	return Shake
}
```

Two things worth having from this. The split we arrived at from replay headers
is in danser too, as `CanBeHitStable` against `CanBeHitLazer`, chosen by a mod
flag — an independent party reached the same conclusion that these are two
rulesets rather than one with a parameter. And there is no fourth rule to find:
danser refuses a good click for exactly the three reasons we do.

So either the fifteen presses are discarded by something outside the hit
policy, or danser does not reproduce stable here either. Both are possible, and
neither can be settled by reading more of danser.

### The way past this

The corpus gives totals over whole plays, which is why a 1% disagreement on
2229 objects is so hard to localise: 23 objects hide easily in four numbers.

A short map does not have that problem. Twenty circles in the pattern under
suspicion, played on stable, and the header's four counts plus the combo are
very nearly a per-object answer — a single wrong verdict moves them visibly.
Building two or three such maps around the Chambarising pattern (160bpm
alternating stream, CS 4.3, OD 6, played deliberately badly) would turn this
from an argument about 23 objects into a measurement.

That is reading the client's *behaviour*, which is what the corpus has been
doing all along and the most reliable source available — the game itself
answering, rather than a reimplementation's opinion of it.

## Old-map stacking: the rule is real, the port still is not

Kona-Chan lost one repeat after the slider-path fix — object #46, cursor 24.2px
from the ball against a 23.04px follow circle, on a play the header records as
a full combo. The gap is 1.16px, and the map is format v4.

The arithmetic says exactly what should close it. Slider #45 ends at
(94.3, 94.3) once its path is trimmed to the authored length; #46 starts at
(96, 96). That is **2.45 osu!pixels apart**, inside the 3px stack distance, so
`applyStackingOld` gives #46 a height of −1 and pushes it down and right by
0.96px. The cursor is then 22.2px away, inside the follow circle, and the
repeat counts.

Implemented, and #46 does come out at `stacked -1` with the ball at (97, 289) —
the gap narrows from 24.2px to 23.3px, moving the right way for the right
reason. It still does not close, and two sliders that were correct without any
stacking (#36 and #37) break. Corpus: 62 error without, 112 with.

So this is the second port of that algorithm to be written and withdrawn. The
rule is not in doubt — one object's position was derived from it and confirmed
by measurement. Something in the port is, and there is now a concrete test for
the next attempt: `#46` must come out at −1 *and* `#36`/`#37` must stay
untouched.

## Rejected: stepping the cursor frame by frame

The last 0.26px of #46 suggested a different answer. Tracking is checked with
the cursor interpolated between recorded frames; the game plays a replay frame
by frame. On the frame before that repeat the cursor was 22.2px away — inside.

Holding the cursor at its last recorded frame instead of interpolating takes
the corpus from 20 exact and 112 error to **17 and 133**. Interpolation is
right; the frames are a sampling of a continuous motion, not the motion itself.

## A note on the corpus figure

`Chambarising` is no longer in the local replay folder, so the corpus is 29
replays rather than 30 and reads 62 rather than 112. That is a change in the
sample, not in the engine — the 50 error it carried is unresolved, not fixed,
and every finding recorded about it above still stands.
