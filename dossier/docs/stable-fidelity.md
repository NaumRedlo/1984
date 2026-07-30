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

> **Answered.** It was a missing rule, and the reasoning above was right that a
> mistuned constant could not do it. See *Chambarising: what it was* below —
> the four presses this section could not explain were being offered a note
> that osu! had not yet finished with.

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

> **Both wrong, as it turned out.** The fourth rule was not in the hit policy
> and not missing from danser: it was in *when the hit policy is asked*. Reading
> `CanBeHitStable` on its own could never have shown it — the answer was in the
> order of the three calls around it, in a different file.

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

## Old-map stacking: the rule is real, and the third port is right

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

That was the second port of that algorithm to be written and withdrawn, and it
failed because the specification it was written from — a summary rather than
the source — was wrong in two places. Reading `applyStackingOld` character for
character found both:

```csharp
Vector2 position2 = currHitObject is Slider currSlider
    ? currSlider.Position + currSlider.Path.PositionAt(1)
    : currHitObject.Position;
...
    startTime = hitObjects[j].StartTime;
```

`Path.PositionAt(1)` is the end of the **drawn curve**, not where the ball
stops — on an even number of slides the ball comes home to the start, and
using its resting place stacks entirely different objects. And `startTime`
advances to the next object's **start**, not its end, so the window creeps
along the pile rather than jumping by each object's duration.

With both corrected the heights come out as the case demands: `#46` at −1,
`#37` at 0, `#36` at +1 — and Kona-Chan's two previously-correct sliders stay
correct. The corpus does not move, which is the right outcome for a rule that
only speaks on maps older than format 6 and, on this one, moves a ball 0.96px.

`#46` still loses its repeat. The stack narrows the gap from 24.2px to 23.3px
against a 23.04px follow circle — the right direction, for the right reason,
and 0.26px short. Whatever closes it is not stacking.

The lesson is about method rather than about osu!. Two ports were written from
a paraphrase of the source and both were wrong; the third was written from the
source and was right first time. A summary of an algorithm is not the
algorithm.

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

## The last 0.26px, and a number that is not the answer

Kona-Chan's `#46` loses its repeat by 0.26 osu!pixels: the cursor is 23.30px
from the ball against a 23.04px follow circle. Everything either side of that
gap has been checked.

- **The stack height is right.** Only one object contributes to `#46` — slider
  `#45`, whose drawn curve ends 2.45px from `#46`'s start — so the height is
  −1, and the shift is 0.96px. Not −2.
- **The ball is where it should be.** The repeat ends the second of three
  slides, so it sits exactly on the path's start; there is no interpolation to
  get wrong.
- **The constants check out.** danser computes the follow circle the way we do:
  `followRadiusFull := player.diff.GetRadius() * 2.4`, and on stable it goes
  through `math87.Mul87` — an emulation of x87's 80-bit arithmetic — which
  moves nothing at the fifteenth decimal, let alone the second.
- **Stepping the cursor frame by frame is still wrong.** Re-measured after the
  stacking fix, in case the earlier answer was contaminated: 17 exact and 123
  error against 21 and 62.

What does close it is a larger follow circle, and the corpus likes that a great
deal:

| multiplier | exact | error |
|---|---|---|
| 2.40 (danser, ours) | 21 | 62 |
| 2.44–2.46 | **23** | **18** |
| 2.50 | 21 | 24 |
| 2.60 | 18 | 35 |

It is not going in. Four replays improve — Fleshgod, yax03 and Kona-Chan all
the way to exact — and **three get worse**: Unsafe Speeds 3→5, NIVIRO 2→4,
Blestyashchiye 1→3. A parameter that has to trade replays against each other is
compensating for something rather than being right, and the source states 2.4
outright.

This is the same shape as the hittable range on `yax03 - down`: a clean corpus
optimum, a wide plateau, a four-fold improvement — and the real cause was
somewhere else entirely, found only because the tempting constant was left
alone. The difference is that this time the real cause has not been found yet.

What the trade tells us: our cursor-to-ball distance is systematically a little
too large during tracking, by something on the order of 1–2%. The ball's
position at a repeat is exact by construction, so the error is in the cursor —
and it is not the interpolation, which has now been ruled out twice.

### When tracking is asked, and why the answer is not "on frames"

If the cursor-to-ball distance runs a little large, the other place to look is
*when* it is measured. This engine samples tracking every millisecond and at
each part's own instant, with the cursor interpolated between recorded frames.
The game reads a replay frame by frame, so sampling only where the recording
says something is the obvious alternative.

It fixes Kona-Chan outright — 55/0/0/0 and 220 combo, exact — and wrecks
everything else: **13 exact and 269 error** against 21 and 62.

Why it works there is instructive. The repeat falls at 55862.35, between frames
at 55847 and 55866. Sampling on frames pins the verdict to 55847, where the
cursor was 22.2px away and inside; interpolating to the true instant puts it at
23.3px and outside. The fix is a 15ms lag, and a 15ms lag applied everywhere
mis-times every other slider in the corpus.

So the effect is real and the mechanism is wrong. Whatever stable does gives a
lag *here* without giving one everywhere.

One more thing distinguishes this replay: **CS 10**, a 9.6px radius and a
23.04px follow circle — by some way the smallest in the corpus, and therefore
the most sensitive to a fixed error in position. Every other replay tolerates
the same 1-2% without changing a verdict. That is consistent with the error
being a small constant fraction rather than anything about DoubleTime, which
three other replays carry with errors of 1 and 2.

Five things checked against these 0.26 pixels, then, and none of them it:
the stack height, the ball's position, the follow multiplier, holding the
cursor at its last frame, and sampling on frames.

### The cursor's coordinates are not shifted or scaled

If the distance runs a fixed fraction large, the cursor's coordinate space is
the natural suspect — a shifted origin, or a scale between the replay's space
and the playfield's. Both are measurable directly: take every press that lands
near an object and look at the vector from the object's centre, over thousands
of clicks.

Two exact no-mod replays, so nothing is mirrored and nothing is inferred:

| | clicks | mean dx | mean dy | spread |
|---|---|---|---|---|
| Epitaph [Expert] | 1647 | −0.17px | −1.42px | 8.7 / 8.5 |
| stresstest | 1976 | −0.56px | +1.20px | 8.4 / 7.8 |

The means disagree in sign on the y axis and sit well inside a spread of eight
pixels: that is two players' aim, not a shifted origin.

Scale, tested by regressing each offset against the object's distance from the
playfield centre:

| | x scale | y scale | correlation |
|---|---|---|---|
| Epitaph [Expert] | 0.9954 | 1.0086 | −0.06 / +0.09 |
| stresstest | 0.9995 | 0.9918 | −0.01 / −0.11 |

Again opposite in sign, with correlations around a tenth. There is no scale
error to find; players simply undershoot the far edges a little, and not even
consistently.

That closes off the whole class. Seven hypotheses have now been measured
against Kona-Chan's 0.26 pixels — stack height, ball position, follow
multiplier, held cursor, frame sampling, coordinate offset, coordinate scale —
and the gap is none of them.

## The score, and two rules that are not in the formula

The score is the one quantity in the engine with an unarguable answer: the
`.osr` header carries the number the client itself arrived at, so every replay
is its own test and no judgement call is needed about what "right" means.

The formula everyone writes down is right and produced scores 4% to 33% over.
Two things underneath it were wrong.

### Halves round to the even side

stable's difficulty multiplier is

```
round((HP + OD + CS + clamp(objects / drainSeconds × 8, 0, 16)) / 38 × 5)
```

and it is a small integer — 4 or 5 for everything in the corpus. One step of it
is a fifth of the score. C#'s `Math.Round` sends a half to the *even*
neighbour; Rust's `f64::round` sends it away from zero. Two maps land on
exactly 4.5:

| map | HP + OD + CS | raw | stable | naive |
|---|---|---|---|---|
| 5067244 | 5 + 9.2 + 4.0 | 4.5 | 4 | 5 |
| 5491890 | 4.5 + 9.2 + 4.5 | 4.5 | 4 | 5 |

Both were 30% over on this alone. The density term is clamped at 16 for every
map anyone actually plays, which is why the sum of the three stats decides it.

### The pieces of a slider are not multiplied

Only whole objects are paid the combo multiplier. A slider's head, ticks,
repeats and end score their flat 10 or 30 whatever the combo:

```csharp
default:                       // circle, slider, spinner
    scoreIncrease = 300;
    addScoreComboMultiplier = true;
    break;
case SliderHeadCircle:
case SliderTailCircle:
case SliderRepeat:
    scoreIncrease = 30;        // no multiplier
    break;
```

Multiplying them cost a uniform 4–8% — the size of the error being just the
fraction of a map's value that sits in slider pieces, which is why it looked
like a constant needing tuning rather than a rule being wrong.

The combo is also read *before* the hit adds to it, and one is subtracted from
that, so the first two objects of a map carry no bonus at all.

### Where it stands

Fitting the multiplier backwards from each header — `(score − flat) /
comboUnits` — is what settled it. Before the two fixes the fitted values were
3.70 to 4.82, a smear; after, they are 4.000 to 5.006, and every one is the
integer the formula gives.

| | replays | worst | exact |
|---|---|---|---|
| stable (ScoreV1) | 11 | 0.12% | 3 |
| lazer (standardised) | 2 | 1.79% | 0 |

The stable residual tracks the judgement, not the arithmetic: the replays that
are still off are the ones whose max combo is also off by one, and one whole
object judged differently at combo 2000 is worth about 120,000 points on a ×5
map — the right order for the 161,230 that Fleshgod is short.

Still missing on the stable side: spinner spins, worth a flat 100 each and 1100
for a bonus spin. Neither map above has a spinner, so they are not what the
residual is. `drainSeconds` also uses the last object's *start* where stable
uses its end; it only feeds the density term, which is clamped everywhere in
the corpus, so it has never been able to matter.

lazer's is a different formula, not a variant:

```
(500000 × accuracy × comboProgress + 500000 × accuracy⁵ × accuracyProgress
  + bonus) × modMultiplier
```

with `comboProgress` weighting each hit by the square root of the combo it
landed on, and `accuracyProgress` a plain count of judgements made over
judgements available. Written as 700000/300000 it came out 7% under; the shape
above is from the source. The remaining 0.6% and 1.8% are the slider head,
which lazer judges on the full 300/100/50 window and we judge hit-or-miss.

Two lazer replays is not a corpus. The number is displayed, and it is not yet
claimed to be exact.

## Health, and why there is no drain formula

Roughly half the corpus arrives with osu!'s own life-bar graph in the header
and half with an empty field — server-downloaded `solo-replay-*` files strip it
entirely. A HUD whose bar appears or vanishes depending on where the replay
came from is worse than one that computes, so the bar is now modelled for the
half that carry nothing, and the half that carry a graph are the test.

The thing worth writing down: **stable has no drain formula.** It solves for
the drain. Starting from a guess of 0.05 per millisecond it plays the map
perfectly, over and over, and adjusts:

| what went wrong on the pass | what moves |
|---|---|
| bar fell below `range(HP, 195, 160, 60)` | drain × 0.96 |
| three combos ended below `range(HP, 198, 170, 80)` | combo bonus × 1.07, gains × 1.03 |
| finished below `range(HP, 198, 180, 80)` | drain × 0.94, both × 1.01 |
| gave back less headroom than `range(HP, 8, 4, 0)` per object | drain × 0.96, bonus × 1.02, gains × 1.01 |

until a flawless play stays above the floor its difficulty sets. Every
"HP drain rate × constant" formula on the internet is wrong for this reason:
the rate depends on where the notes are, not only on HP. The same HP 5 setting
gives 0.0130 on one map in the corpus and 0.0306 on another — a factor of two
and a half, from the map alone.

Two more things that are not obvious from the numbers:

* A 50 is worth **eight times** as much at HP 0 as at HP 5, and a 100 likewise;
  a 300 is worth six everywhere. That asymmetry, not a gentler drain, is what
  makes an easy map forgiving.
* The combo-end bonus is 14 against a 300's 6. It is what actually keeps a
  player alive through a hard map, which is why breaking a combo early costs so
  much more than the note itself.

The calibration's own gains and the live play's do not quite agree — the
calibration credits one slider "repeat" per slide where the live pass credits a
head, each repeat and an end. That is stable's inconsistency and danser
reproduces it, so it is reproduced here too.

### Measured

Sixteen replays carry a graph, HP 1 to 7. Compared at osu!'s own sample points,
so nothing is invented between them:

| | value |
|---|---|
| mean divergence | 0.020 of the bar |
| best replay | 0.003 |
| worst replay | 0.054 |
| bias | 11 replays low, 5 high |

The bias splitting both ways is the useful part: a model that was wrong would
lean one way. What is left is the judgement. The three worst outliers are all
Chambarising, where we already know we credit notes stable does not — one
replay judges 23 fewer misses, and at HP 1 each of those is a swing of about
8% of the bar between a +6 and a −9.8, which covers the 0.14 to 0.41 gaps
exactly.

The slight lean low is consistent with the one piece not yet modelled: spinner
spins, worth 1.7 each, which the calibration counts and the live pass does not.
Not confirmed — there is no spinner-heavy replay with a graph in the corpus to
settle it on.

lazer's model is a different shape and much simpler — a flat table of gains out
of one, and a binary search for the drain that leaves a perfect play at
`range(HP, 0.99, 0.9, 0.4)` at its lowest. It has no ground truth here at all:
every lazer replay in the corpus came from the server with the graph stripped.
It is implemented from the source and not yet checked against anything.

## Chambarising: what it was

Five replays of the map, played deliberately badly by different people, ended
a question that one replay could not. All five disagreed the same way — we
credited hits osu! called misses — and the size of the disagreement tracked how
bad the play was:

| player | accuracy | objects we over-credited |
|---|---|---|
| Deeo_XD | 91.6% | 4 |
| Deom0ng | 68.9% | 10 |
| Uika Misumi | 36.9% | 10 |
| sw1t | 38.4% | 23 |
| kazak1865 | 33.2% | 24 |

A clean play is nearly right and a mashed one is a per cent out. Whatever the
missing rule was, it only spoke when notes were being dropped — which is note
lock territory, and the lock was already implemented.

The answer is two milliseconds, and it is two separate off-by-ones.

**The game's own comparison is strict.** A circle writes itself off at

```go
if time > int64(circle.hitCircle.GetEndTime())+player.diff.Hit50 && !state.isHit {
```

so the earliest millisecond at which a note can stop blocking is
`start + window50 + 1`, not `start + window50`.

**And a click does not wait for that sweep.** At every call site the order is
the same:

```go
controller.ruleset.UpdateClickFor(controller.cursors[i], replayTime)
controller.ruleset.UpdateNormalFor(controller.cursors[i], replayTime, processAhead)
controller.ruleset.UpdatePostFor(controller.cursors[i], replayTime, processAhead)
```

Clicks are offered to the objects first; only afterwards is anything swept up.
So a click is tested against the world as the *previous* update left it — one
millisecond earlier, the game working in whole milliseconds — and a note whose
window shut a moment ago is still in the way.

This engine was testing against the click's own instant, with a loose
comparison. Both wrong, one millisecond each.

### Why this is a rule and not a constant

The obvious objection is that "+2ms" is a fitted number. The corpus says
otherwise:

| grace given to a spent note | total count error |
|---|---|
| 0ms (what this engine did) | 198 |
| 1ms | 114 |
| **2ms** | **70** |
| 3ms | 246 |
| 4ms | 494 |
| 6ms | 680 |
| 10ms | 1020 |
| 16ms | 1678 |

A knife edge, not a basin. A constant fitted to the data would sit in a broad
minimum and be worth arguing about; this one is worth 128 either side of it.

The whole-frame reading — that osu! only sweeps when a replay frame arrives,
which would be about 16ms — is refuted by the same table. The game updates far
faster than a replay records.

### What it cost and what it bought

Nothing. Not one replay in the corpus got worse:

| | before | after |
|---|---|---|
| total count error | 198 | **56** |
| exactly right | 21 | 21 |
| replays made worse | — | **0** |

The five Chambarising replays went 8→2, 24→6, 30→4, 48→4 and 50→2. Every other
replay is untouched: the rule only speaks where a click arrives within two
milliseconds of a note's window closing, which on a clean play never happens.

The last 14 of that 56 is the two lazer replays, which is a separate question.

### A second thing fell out of it

Because a note can now be reached after its window has gone, something has to
happen when a click reaches one. osu! judges it a miss on the spot:

```go
} else if int64(delta) < player.diff.Hit50 {
    return Hit50
}
return Miss
```

rather than leaving it to be swept later. So the miss is dated to the click,
not to the end of the window — which is where the player saw it happen, and
what the timeline and the health curve should show. That accounts for the
difference between 70 and 56 in the table above.

## The lazer replays: a slider is not one thing

The two lazer replays in the corpus were 10 and 8 count-units out, and both
disagreed the same way: we handed out 300s where lazer handed out 100s. That is
not a lock question and not a window question. It is a question about what a
slider *is*.

lazer took the slider apart:

```csharp
// Slider.cs
public override Judgement CreateJudgement() => ClassicSliderBehaviour
    ? new OsuJudgement()
    : new OsuIgnoreJudgement();

// SliderHeadCircle.cs
public override Judgement CreateJudgement() =>
    ClassicSliderBehaviour ? new SliderTickJudgement() : base.CreateJudgement();
```

Without the Classic flag the slider itself is `IgnoreHit` — worth nothing,
counted as nothing — and its head is an ordinary circle on ordinary windows. So
the 300 or 100 that lands in a lazer score for a slider is the *head's*, and a
slider tracked flawlessly from a head hit sixty milliseconds late is a 100.

stable keeps the slider whole: the head is a flat thirty points whenever it
lands, and the slider's verdict comes from the fraction of its pieces caught.
Everything caught is a 300, however late the head was.

That the counts still sum to the object count under either reading is what let
this hide: one verdict per object either way, just a different one.

| | before | after |
|---|---|---|
| Unlucky Morpheus — Majotachi | 10 | **counts exact** (combo −1) |
| Utsu-P — Imperfect Animals | 8 | **exact** |
| tokken — Otfix AR10 (EZ) | 2 | 4 |
| corpus total | 56 | **40** |
| exactly right | 21 | **22** |

### Two threads left open, and neither is this rule

**One combo on Majotachi.** Every one of its 1029 verdicts now matches and the
max combo is 959 against 960 — one slider piece, a tick or a tail, out of a
1343 maximum. That is the slider-tracking noise already known about, not a
rule.

**Two slider heads on Otfix.** This replay went from 2 to 4, and the reason is
worth stating rather than hiding: it did not get worse, it got *specific*. One
slider we score as a total miss is a 300 to lazer, which was already true
before this change. One more has a head we call missed and a body we track,
which the old parts-summary quietly rounded up to a 100 — the right answer by
accident. Both are the same question, and it is about whether the head was hit
at all, not about what the slider is worth once it was.

### And the score, twice

The counts being right did not make the score right, and the two things it was
wrong about are both worth having.

**The head again.** Our judge records a slider head as hit or not, because a
flat thirty points is all stable asks of it. lazer wants the window verdict, so
the score now recovers it from the timing error the judge already kept.

**The combo half is weighted by the maximum, not by what was earned.**

```csharp
protected virtual double GetComboScoreChange(JudgementResult result) =>
    GetBaseScoreForResult(result.Judgement.MaxResult) * Math.Pow(result.ComboAfterJudgement, COMBO_EXPONENT);
```

`MaxResult`. A hundred carries its full three hundred into the combo half,
because that half is about the combo — the accuracy is applied to it separately,
once, in the total. Weighting it by what was earned charges the accuracy twice.
A miss needs no special case: it leaves the combo at zero and the root of zero
is zero.

| | before | head verdict | and the combo weight |
|---|---|---|---|
| Majotachi | +0.60% | −0.65% | **−0.14%** |
| Imperfect Animals | +1.79% | −0.68% | **+0.10%** |

Both were under by two thirds of a per cent after the first fix and straddle
zero after the second, which is what a right model looks like against a wrong
one: the residual changes sign between replays instead of leaning.

### The mod we could not see, and now can

Both halves of the rule hang off `ClassicSliderBehaviour`, and lazer's Classic
mod sets it. The `.osr` header carries the legacy mod bitmask and Classic has no
legacy bit, so from the header alone a Classic score is indistinguishable from
an ordinary one.

It is not the header alone. `LegacyScoreEncoder` appends one more length-
prefixed block after everything stable understands — the same LZMA-alone stream
the frames use, holding a JSON document — and it is now read.

The mods are the least of what is in it:

```json
{
  "client_version": "2026.417.0-tachyon-linux",
  "mods": [],
  "statistics": {
    "miss": 2, "meh": 1, "ok": 23, "great": 1003,
    "large_tick_hit": 53, "ignore_hit": 261, "slider_tail_hit": 261
  },
  "maximum_statistics": { "great": 1029, "large_tick_hit": 53, "slider_tail_hit": 261 }
}
```

That is a count **per judgement type**, where the legacy header has four numbers
with every slider folded into them. It is the closest thing to a per-object
answer any replay carries, and it is the ground truth the open questions above
have been missing: 261 slider tails and 53 large ticks are numbers to check our
tracking against directly, rather than inferring one dropped part from a combo
that came out one short.

### Classic is three switches, not one

```csharp
public Bindable<bool> NoSliderHeadAccuracy { get; } = new BindableBool(true);
public Bindable<bool> ClassicNoteLock { get; } = new BindableBool(true);
public Bindable<bool> ClassicHealth { get; } = new Bindable<bool>(true);
```

Each can be turned off on its own, so "a Classic score is a stable score" is
wrong: it can have stable's note lock and lazer's sliders, or the reverse. The
ruleset stopped being a two-valued enum over that — it is a client plus the
switches, and each rule reads the one that governs it. All three default to on,
so a setting the replay does not mention is *on*; reading an absent key as false
would quietly undo half the mod.

Nothing in the corpus has Classic, so this wiring is right by construction and
unverified by measurement. It is written down here so that when a Classic replay
does arrive, what it is being judged by is a matter of record rather than of
memory.

## Checking the tails against lazer's own count

The block lazer appends carries a count per judgement type, so the two open
lazer questions stopped being arguments and became measurements. What it says,
against what we say:

| | | ours | lazer's |
|---|---|---|---|
| Majotachi | slider tails | 260 | **261** |
| | large ticks | 53 | 53 |
| | everything else | — | matches |
| Imperfect Animals | slider tails | 508 | 508 |
| | large ticks | 41 | 41 |
| Otfix (EZ) | slider tails | 42 | **49** |

### The one combo on Majotachi is one slider tail

Not a tick, not a repeat — `slider_tail_hit` 260 against 261, with every other
type exact. The slider is #886 at 132117ms, and the tail is checked at
132169ms with the cursor **90.1 pixels** from the ball against a follow circle
of **90**.

A tenth of a pixel. There is nothing to fix there: any constant that would
recover it is a constant chosen to recover it.

### `ignore_miss` is not what it looks like

Imperfect Animals reads 9 against lazer's 21, with all 508 tails and all 41
ticks exact. The twelve are not sliders at all — the same block says
`large_bonus` 5 against a maximum of 17, and a spinner's bonus spin that is not
achieved is an `IgnoreMiss`:

```
9 dropped tails + 12 unachieved bonus spins = 21
```

Spinner spins are the one piece of the object model this engine still does not
have, and this is the second place it has surfaced — the health model leans
low for the same reason.

### A late head hands the slide over, in lazer only

Otfix drops seven tails lazer keeps, and five of them have the cursor plainly
*inside* the follow circle when the tail is checked — 60, 73, 96, 97, 98 pixels
against 109. So tracking was lost earlier and never regained, and the question
is when it starts.

```csharp
public void PostProcessHeadJudgement(DrawableSliderHead head)
{
    if (!head.Judged || !head.Result.IsHit) return;
    if (!IsMouseInFollowArea(true)) return;
    ...
    updateTracking(allTicksInRange || IsMouseInFollowArea(false));
}
```

`IsMouseInFollowArea(true)` — the *expanded* area. Landing the head starts the
slide from 2.4 radii, where ordinarily tracking may only be picked up from
within the ball itself. It only matters on a fast slider hit late: by the time
the click is judged the ball has travelled, and demanding the cursor be back on
top of it drops a slider the player is plainly holding.

Four of the seven come back. And the rule is lazer's alone, which the corpus
confirms rather than assumes — handing stable the same behaviour takes it from
22 exact replays to 16 and doubles the count error:

| | exact | count error |
|---|---|---|
| lazer only | **22** | **40** |
| both clients | 16 | 82 |

### Where the lazer side stands

| | before this pass | after |
|---|---|---|
| Majotachi | 10 units | counts exact, one tail |
| Imperfect Animals | 8 units | exact; `ignore_miss` is spinner bonuses |
| Otfix (EZ) | tails 42/49 | tails 46/49 |

What is left on Otfix is three tails and two objects, and the two are **not**
slider heads after all.

Nine sliders lose their head on this replay, and not one has a click both inside
the fifty window and inside the circle: the nearest are 250ms early at 9 pixels,
and 215ms late at 51. No head there was clickable and clicked, so lazer cannot
be crediting one. The two must be circles — two of the fifty-five we call missed
where a click landed for lazer and did not for us, which makes it a question
about which object a click is offered to under `StartTimeOrderedHitPolicy`,
not about sliders at all.

That is as far as totals reach. Identifying *which* two needs a per-object
answer, and the one replay that could give it carries no life-bar graph to
localise them in time. It rests there rather than being guessed at.

One thing was ruled out along the way. A click 250ms early takes the note with
it in this engine, and lazer has a rule that looks like it should differ:

```csharp
// Generally when the user has hit way too early.
if (result == HitResult.None)
    return ClickAction.Shake;
```

But `ResultFor` only returns `None` outside *every* window, and osu!'s miss
window is 400ms — so at 250ms early it returns `Miss`, the click is a hit
action, and lazer spends the note exactly as we do. The rule only speaks past
400ms, where our hittable range already refuses the click.

## The tail is decided over a window, not at an instant

Otfix dropped three tails lazer kept, and the reason is that a tail is not a
single check at all:

```csharp
case DrawableSliderTail:
    if (timeOffset < SliderEventGenerator.TAIL_LENIENCY) return;   // -36
    ...
if (Tracking)
    nestedObject.HitForcefully();
else if (timeOffset >= 0)
    nestedObject.MissForcefully();
```

The hit is taken the first frame tracking is true; the miss is only written
once `timeOffset >= 0`. So every frame from thirty-six milliseconds early to
the slider's own end is another chance, and a player who lets go a moment
before the end keeps the tail — which they are entitled to do.

Where that window sits took reading the generator, because there are two
candidate times and only one is the tail's:

```csharp
double legacyLastTickTime = Math.Max(startTime + totalDuration / 2, (finalSpanStartTime + spanDuration) + TAIL_LENIENCY);
// ... a separate event ...
yield return new SliderEventDescriptor
{
    Type = SliderEventType.Tail,
    Time = startTime + totalDuration,     // the true end
};
```

The `max(duration/2, duration − 36)` form is the **LegacyLastTick** — stable's
tail, and a single instant. lazer's Tail is at the slider's true end with the
window running back from it. Two different objects that happen to be 36ms
apart, which is exactly how they get confused.

| | tails, ours | lazer's |
|---|---|---|
| Otfix (EZ) | 46 → **49** | 49 |
| Majotachi | 260 | 261 |
| Imperfect Animals | 508 → **509** | 508 |

Otfix is now exact on tails. Imperfect Animals went from matching to one over,
and that is worth stating plainly rather than smoothing: the window can only
add tails, so we now credit one lazer does not — the slider at 41115ms, whose
cursor is 92 pixels from the ball when the window opens and comes back inside
it before the slider ends. The rule is not in doubt; our tracking on that one
slider is.

A guard from the same method was tried against it and is wrong as read:

```csharp
if (!slider.HeadCircle.Judged)
    return;
```

Holding the window shut until the head resolves takes Otfix from 49 back to 47
and leaves Imperfect Animals where it was. Reverted.

### Sampling rate is not the answer either

Tracking is evaluated here every millisecond, and the game evaluates it on
frames. Sampling on the replay's own frames instead changes **nothing**: the
same 22 replays exact, the same count error, every per-type figure identical.
Recorded because it is an obvious suspect and it is now a closed one.

## The fail, as lazer plays it

Everything before this was an invention: a slow-down eased into the death, then
a hard stall, then a stall with the music dragged down beside it. None of it is
what either client does. lazer's is one file, `FailAnimationContainer`, and it
is all constants:

```csharp
private const float duration = 2500;

this.TransformBindableTo(trackFreq, 0, duration);        // the music winds to nothing
drawableRuleset.Playfield.HitObjectContainer.FadeOut(duration / 2);
redFlashLayer.FadeOutFromOne(1000);                      // Color4.Red.Opacity(0.6f), additive
Content.ScaleTo(0.85f, duration, Easing.OutQuart);
Content.RotateTo(1, duration, Easing.OutQuart);
Content.FadeColour(Color4.Gray, duration);
```

Two and a half seconds. The clock stops — the play is over, so the field is
frozen at the instant it stopped — and what follows is real time, with the
notes gone by halfway and a red flash across the first second of it.

The timing is taken and the movement is not. lazer tilts the frame a degree and
drops it; this pulls in — hard at the death and then still closing, for as long
as the music has left — and lets go in the last half second, back to full size
with nothing on it, which is the field the play started from.

The two halves are shaped against each other. The squeeze takes most of its
distance immediately and then creeps, so the frame is still tightening while
the sound is still dying and the two arrive together. The release gets the last
fifth to itself, which at two and a half seconds is half a second: long enough
to see, short enough to read as something let go rather than something eased.

Nothing fades. lazer takes the notes away over the first half and drains the
colour out of the rest, and both were here until the whole thing read as the
render giving up rather than the play ending — a picture that dims while it
moves says the video is finishing, not the play. The frame keeps everything it
had, springs back to full size, and is gone between one frame and the next.

Then a second of nothing, which is what makes the cut read as an ending rather
than as a dropped frame. Two constants, `FAIL_ANIMATION_MS` and
`FAIL_EMPTY_MS`, both exported from the renderer — the encoder has to leave
room for exactly as much tail as the renderer draws, and two numbers that must
agree are one number with a hazard attached.

Two reasons. A tilt is a permanent state — the frame is left crooked and
nothing puts it back — where a squeeze is a movement that completes, which is
what a render wants at its *end* rather than in the middle of a stream. And
these clips get cut together with others: one that finishes level can be
followed by anything, and one that finishes at a slight angle cannot.

### Two places it could not be copied outright

**The red flash.** lazer's 0.6 additive lands on a dimmed beatmap background
with a lit playfield over it, and reads as a flash across a picture. Here the
field is very nearly black, so the same value has nothing to compete with: it
floods the frame into a flat red card and holds it there for a second. It is
drawn at half the opacity and squared on the way out, so it is a blow rather
than a wash. The constant is lazer's; the surface is not.

**The music.** `TransformBindableTo(trackFreq, 0, duration)` is a continuous
ramp, and `asetrate` — the one filter that takes pitch down with tempo, which
is the sound wanted — reinterprets a stream at one fixed rate and cannot ramp
at all. So the tail is cut into ten steps, each slower than the last, each
consuming only as much source as it plays, and concatenated. A staircase where
lazer has a curve; at ten steps over two and a half seconds the ear hears a
slide.

Measured on the render, as zero crossings per second — a rough stand-in for
pitch:

| | during play | +1.5s | +2.0s | +2.5s |
|---|---|---|---|---|
| | ~1800 | 992 | 624 | 340 |

### The play stops where the bar does

The header says *how many* objects were judged, and the last of those resolving
was being used as the moment the play ended. It is the wrong answer. The final
fourteen of them are one unbroken miss streak — the player had stopped hitting
anything — and their windows go on shutting for more than a second after the
bar is visibly empty. The render drew a dead player still playing, and then a
fail animation over the corpse.

The bar is what the moment *is*, so the model's own death takes it where that
comes first. The counts stay the header's: it says 258 objects were judged and
that is a fact about the play whatever moment the animation starts on.

The two readings disagree by about a second here, and that is a real gap rather
than a matter of taste — osu! went on judging through misses our drain says
were already fatal, which means our drain is fast on this map. It is one
replay, and it is the only failed one in the corpus. What is not tolerable is
showing both readings at once.

### What is not modelled

lazer drops each object independently — four hundred pixels down, half size, on
its own random rotation — so the notes rain rather than sink together. That is
a per-object transform where this is a per-frame one.

## Two things the fail render was getting wrong

### The music slowed by the wrong amount

The picture drops to four tenths speed at the fail and the audio was told to
match:

```
asetrate=44100*0.4
```

`asetrate` does not slow anything. It *reinterprets* the stream as being at the
rate given, and the slowdown is whatever ratio that makes against the rate the
stream is actually at. osu! ships 48kHz audio, so naming 44100 slowed the music
to 0.3675 — a slowdown, near enough to sound deliberate, and wrong enough that
the two came apart from the moment they were supposed to give out together.

The fix is not a better number. The stream is resampled to a known rate before
the split, so the number in the filter is a fact about the stream rather than a
guess about the source.

### The health bar was drawing a ruled line

osu!'s life-bar graph is about a hundred samples across a whole map. It is a
record of the curve, not the curve, and between two samples it says nothing at
all. On the corpus's one failed replay it reads full at 76126ms and empty at
78158, so the bar slid down a perfectly straight two-second line through a
death that took half of one:

| | 76.4s | 76.6s | 76.8s | 77.0s | 77.2s |
|---|---|---|---|---|---|
| the graph, interpolated | 0.87 | 0.77 | 0.67 | 0.57 | 0.47 |
| the model | 0.89 | 0.56 | 0.28 | 0.10 | **0.00** |

The model is what the player saw — the game keeps health continuously and
compresses it for the scoreboard afterwards — so the model draws now and the
graph checks. Preferring the record over the model was the right instinct about
*accuracy* and the wrong one about *resolution*: a bar exists to show a
collapse happening, and the record cannot.

It is not free. The model has this player dead at 77.2s where the graph reaches
zero at 78.2s, and the play does not stop until 78.4s — a second of empty bar
under a play still running. Mean divergence over the seventeen replays that
carry a graph is unchanged at 0.018 of the bar, so this is one replay's
residual rather than a new error, but it is the residual now being drawn.


## The corpus, after fetching what was already there

142 replays sat on disk and 35 of them could be measured. The other 107 were
not missing — their beatmaps were. Eighty-eight distinct maps, all of them a
mirror lookup away.

| | before | after |
|---|---|---|
| replays measured | 35 | **117** |
| exactly right | 21 | **60** |
| lazer among them | 3 | **15** |
| total count error | 40 | 278 |
| still unmeasurable | 107 | 25 |

The error going up is the point. Per replay it went from 1.1 to 2.4, which
says the old set was flattering — thirty-five replays, most of them one
player's, and the engine had been tuned against them for months. Twelve of the
maps are not on the mirror and thirteen replays failed to fetch; those 25 stay
unmeasurable until the maps turn up somewhere else.

What it buys is not a better number. It is that the open questions stop resting
on one replay each:

| | worst rows now |
|---|---|
| 52 units, combo +1 | Uika Misumi — Bug |
| 32 units, combo −154 | legusshhka — Bon Appétit S |
| 18 units, combo +4 | N1sh1mia — Smag |
| 14 units, combo +3 | N1sh1mia — Shiroi Yuki no Princess |

A combo out by 154 on one replay is a different kind of fault from anything in
the old set, and it is now visible.

## Keeping the quotes honest

Forty-six blocks of somebody else's source are quoted in this file and in the
engine, and lazer ships every week. A quote can be checked where a link cannot,
but only while the file it came from stays put — and nothing was watching.

`tools/upstream.tsv` names the twenty-four files the rules were read from, what
is taken from each, and a hash of the content as read.
`tools/check-upstream.sh` refetches them and says which have moved, with a link
to that file's commit history; `--update` re-pins once a diff has been read.

This is the shape the problem takes here. A bot that wants pp can depend on
`ppy.osu.Game` from NuGet and be current by bumping a version, because it runs
the real thing. This engine cannot: half of what it implements is *stable*,
which is not in lazer's source at all, and the other half is in Rust. Reading
the source and quoting it is the only route, so the quotes need a tripwire
rather than a subscription.

## `dossier corpus`

The measurement every change is judged by, which lived for months as a shell
script assembled from `judge`, `grep` and an awk one-liner. A number that
cannot be reproduced exactly is not a measurement.

```
dossier corpus --songs ~/.osu/Songs --strict 278 <replays>
```

One line per replay that disagrees, sorted worst first, with lazer and stable
marked; a total; and a non-zero exit when the total is worse than the ceiling
it is held to.


## The mod multiplier is not a constant, and never was one number

lazer's per-mod multiplier stopped being a property of the mod:

```csharp
[Obsolete("This property is no longer used to calculate the score multiplier.
           Use `Ruleset.CreateScoreMultiplierCalculator()` instead.")]
public virtual double ScoreMultiplier => 1;
```

It is a calculator belonging to the ruleset, and there are two of them —
`OsuScoreMultiplierCalculatorV1` and `…V2` — with the rebalance landing at
replay version 30000017. Both are implemented here. The differences are not
tweaks:

| | V1 | V2 |
|---|---|---|
| Easy | 0.5 | 0.8, less 0.1 per extra life |
| HardRock | 1.06, or 1 if configured | 1.09 flat |
| HalfTime | 0.30 | 0.55 |
| DoubleTime | 1.10 | 1.23 |
| SpunOut | 0.9 | 0.95 |
| Classic | 0.96 | 0.985, or 0.96 without its note lock |
| Hidden+Blinds | 1.06 × 1.12 | **1.24**, priced once |

That last row is the one worth reading twice. V2 registers *combinations*, and a
combination consumes its mods so they are not also priced singly:

```csharp
if (remainingModTypes.IsSupersetOf(combination))
{
    result *= multiplier(instances);
    remainingModTypes.ExceptWith(combination);
}
```

### The version stamp does not say which was used

The obvious key is the replay's own version field, and it is wrong. A replay in
the corpus stamped 30000016 — before the rebalance — is scored with V2's
DoubleTime. Its judgement is exact to every one of lazer's judgement types, so
the 10% the V1 table left over could only be the multiplier.

The block lazer appends settles it without guessing, because it carries the
total *before* the mods:

| | stamp | `score / total_score_without_mods` | which table |
|---|---|---|---|
| DoubleTime | 30000016 | **1.2300** | V2 |
| Easy | 30000017 | **0.8000** | V2 |

So the multiplier is read where the replay states it and looked up only where it
does not. The tables stay because the field is not in every replay — and
because getting them from the source is how the reading was checked in the
first place: 1.23 and 0.80 to four decimals, both.

### A failed play stopped scoring at the wrong place

Found in the same sweep, and worth more than the multipliers. The judge walks
the whole map and calls everything past a death a miss; the score was reading
its total at the end of that walk. On a lazer play that died a third of the way
in, that is 885 invented misses dragging the accuracy down: **−92%** against
the header.

The counts had been right about this for months — they are taken over the
objects the play reached — and the score simply was not asking.

| | before | after |
|---|---|---|
| the failed lazer play | −92.13% | **+0.04%** |
| DoubleTime, 30000016 | −10.52% | **+0.05%** |
| NoFail ×2 | exact | exact |

### What `corpus` measures now

The score, alongside the counts, because the two move independently: a replay
whose four counts are exact can still be scored a hundred per cent wrong, which
is exactly how the failed play hid.

```
62 exact of 119 (15 lazer), total count error 278
score compared on 118, worst 29.79%, within 0.5% on 101
```

Compared on 118 rather than 119 because one replay carries stable's **ScoreV2**
mod, which replaces ScoreV1 with a millionth-scale formula that is not
implemented here. It is marked incomparable rather than counted: a single
unimplemented mode read as a 754% error, which would have swamped the statistic
it appeared in.

## A fortieth of a per cent of the radius

Two replays in the widened corpus had a combo out by 154 and 75 while their
counts were nearly right — a shape nothing in the old set had. On the smaller
one, the break that cost seventy-five links was this:

```
88758  press  refused by the lock  #419 — blocked by #418, due 88770ms and still unjudged
       …the same press, measured against #418:  37.39px  off it
       radius 37.38
```

A hundredth of a pixel outside the circle. We refuse it, the note goes unjudged,
and the lock cascades from there.

The positions were checked against the map first and match to the pixel; no
stack was involved. So it was the radius, and the answer is not a tuned
allowance but ppy's own, with its own name and its own comment:

```csharp
// Builds of osu! up to 2013-05-04 had the gamefield being rounded down, which caused incorrect
// radius calculations in widescreen cases. This ratio adjusts to allow for old replays to work
// post-fix, which in turn increases the lenience for all plays, but by an amount so small it
// should only be effective in replays.
//
// It works out to under 1 game pixel and is generally not meaningful to gameplay, but is to
// replay playback accuracy.
const float broken_gamefield_rounding_allowance = 1.00041f;

return (float)(1.0f - 0.7f * DifficultyRange(circleSize)) / 2 * (applyFudge ? broken_gamefield_rounding_allowance : 1);
```

"Not meaningful to gameplay, but is to replay playback accuracy" is a
description of this engine's entire purpose. At CS 3.8 the allowance is fifteen
thousandths of a pixel, and 37.376 × 1.00041 is 37.3913 — which takes the click
above.

It rides on the *scale*, so the stack offset carries it too: `StackOffset =>
StackHeight * Scale * -6.4f` against `Radius => OBJECT_RADIUS * Scale`.

| | before | after |
|---|---|---|
| exactly right | 62 | **65** |
| total count error | 278 | **238** |
| score within 0.5% | 101 of 118 | **104** |
| BLACKPINK — JUMP | 6 units, combo −75, score −29.8% | **exact** |
| Bon Appétit S | 32 units, combo −154, score −20.0% | 6 units, combo exact |

Worth noting what this is not. The docs above record shrinking the radius being
tried against Chambarising and rejected for moving the wrong totals. That was a
constant looked for in the data. This is a constant found in the source, whose
comment says what it is for, and it happens to be the other direction.


## Every replay on disk is now measured, or has a reason it cannot be

Twenty-five replays sat outside the corpus because their beatmaps were absent —
twelve maps "not on the mirror" and thirteen fetches that had failed. That is
the worst state a replay can be in. A replay that disagrees with us is a
question; a replay that is not measured at all is not even that.

The bot the fetching was modelled on (`Airkek/osubot-telegram`) does not use a
mirror for the file at all:

```csharp
// performance-server/src/OsuPerformanceServer/Program.cs
string baseUrl = builder.Configuration["BEATMAP_DOWNLOAD_BASE_URL"]
    ?? "https://osu.ppy.sh/osu/";
```

`https://osu.ppy.sh/osu/<id>` is the official raw `.osu`, no key required. A
mirror has what somebody uploaded to it; ppy has what exists. "Not on the
mirror" was never the same statement as "gone", and treating the two as one is
what cost twelve maps.

What a mirror *is* needed for is the step ppy has no endpoint for: turning an
MD5 into an id. A replay names its map by hash alone. Two mirrors are asked,
because their indexes differ — catboy answers for ranked and loved and 404s on
everything else, while osu.direct also carries graveyard, which is most of what
a replay from a friend is played on. Four of the last six came back from that
second lookup.

Cheaper than either: a server-downloaded replay is named
`solo-replay-osu_<beatmap>_<score>`. The id is in the filename and no third
party is involved at all. Nine of sixteen were resolved that way.

`tools/fetch-maps.py` does this, and checks three things before keeping a file,
following osu!'s own `BeatmapStore`: the header must read `osu file format v`,
the size must be under 50MB, and **the MD5 must be the one the replay asked
for**. The last is not paranoia. ppy serves the map as it is *now*, and a map
revised since the replay was set comes back a different file that would be
judged against the wrong notes without ever looking wrong. Two responses were
rejected on other grounds and one class of failure is worth naming: ppy answers
`200` with a zero-byte body for a map deleted since it was played.

| | before | after |
|---|---|---|
| replays measured | 119 | **137** |
| exactly right | 65 | **79** |
| lazer among them | 15 | **16** |
| total count error | 238 | 278 |
| unmeasurable | 25 | **5** |

The error rising is the same effect as last time: per replay it is 2.0 → 2.03,
so the eighteen replays that joined are no worse than the ones already there.

The five that remain are not a backlog. Three are mania, which this engine does
not simulate at all. The other two are genuinely gone — one deleted from ppy
and never mirrored, one whose hash no mirror knows. There is no fetch that
recovers them and none is worth writing.

Twelve more replays turned up in a directory the corpus had never been pointed
at, which is the real lesson: the corpus was whatever a `find` command happened
to match on one machine. That is the next thing to fix.


## Two movements at the ends

The render opened and closed on hard cuts. Both are now movements, and they are
deliberately not the same movement.

**The opening** comes up from black over 450ms, squared so it leaves black
quickly and arrives gently — a linear ramp on a nearly black field spends most
of its length looking like nothing is happening. It is kept under the lead-in,
so it is over before the first note is approaching and never competes with one.
Without it the render opens on a lit but empty field, which reads as a file
starting mid-thought.

**The ending** gained a third phase. It was: the frame closes in, springs back
to full size with everything still on it, and is gone between one frame and the
next. That last step was a cut, and a cut in that position reads as a dropped
frame. Now the frame lets go, and *then* clears over 220ms — fast at first, so
it is gone early and the tail of the movement is only there to keep it from
being a cut.

The order matters and is the reason this is a separate phase rather than an
opacity on the existing one. A fade running underneath the squeeze was tried
first, twice, and both times it read as the render giving up rather than the
play ending. The frame has to complete its movement while it is still whole.

`fail_tail_ms()` in `video.rs` is the sum of all three, and there is now a test
holding it to that sum — a tail short of it cuts the file mid-movement, and a
truncated video is still a valid video, so nothing else would catch it.


## The corpus is now a set, not whatever `find` matched

Twelve replays turning up in a directory nobody had pointed the corpus at was
the symptom. The disease was that the corpus had no definition: it was the
output of a shell command, retyped each time, and every number this project
published was taken from whatever that command happened to match.

`tools/corpus.tsv` is the definition. One row per replay, keyed by the MD5 of
the `.osr` — filenames vary, the same play sits in two folders, a download gets
`(2)` appended, and none of that changes the hash. Each row carries the map it
needs, that map's id, and what the replay is expected to do.

```
replay_md5  beatmap_md5  beatmap_id  error  combo  score  name
```

`dossier corpus --expect tools/corpus.tsv` checks against it and
`--update-expect` rewrites it, which is the same shape `check-upstream.sh`
already had.

Three things this catches that a total held to a ceiling cannot:

**Trades.** Two replays getting worse while a third gets better leaves the sum
where it was. Faking twenty-three rows to zero produced twenty-three named
lines, not one number that failed to move.

**A shrinking set.** A corpus that loses replays reports a smaller total, which
looks like progress. Absent rows are now listed by hash, and under `--strict`
they fail the run.

**Duplicates.** The first run against the manifest found **twelve replays
counted twice**, present in two directories each. Deduplicating by hash moved
the real numbers to 72 exact of 128 with a total of 260 — from 79 of 137 at
278. Nothing got worse; the old figures were counting a dozen plays twice.

| | as reported | deduplicated |
|---|---|---|
| replays measured | 137 | **128** |
| exactly right | 79 | **72** |
| total count error | 278 | **260** |

The replays themselves are not in the repository and will not be — they are
other people's plays. The maps are public, though, and the manifest is enough
to fetch every one of them: `tools/fetch-maps.py --manifest tools/corpus.tsv`
rebuilt all 117 into an empty directory from the pinned ids alone, and the
corpus measured identically against it. The ids are pinned for exactly that
reason — with an id the map comes straight from ppy, and no mirror has to still
be answering hash lookups a year from now.

One bug fell out of writing it. `trim_end()` on a manifest line takes the tab
off a row whose last field is empty, leaving six fields where there are seven —
so a replay whose name could not be read would have been rejected as malformed.
Caught by the round-trip test rather than by a corpus run, which is the whole
argument for having one.


## A render that failed hung instead of saying so

A render on a one-core server stopped at 6600 of 6849 frames and came back with
nothing but the progress line. Two bugs, and between them they made a stated
failure look like a silent one.

**The deadlock.** Workers wait for a free buffer in `rx.recv()`, which wakes
only when its sender is dropped. The senders lived in the enclosing function,
not in the `thread::scope` closure, so an early return from the writer left
every idle worker waiting forever — and `thread::scope` waits on its threads.
An ffmpeg that died mid-render therefore hung the program rather than
reporting. `let returns = returns;` inside the closure moves them in, so both
exits drop them.

It only bites on the failure path, which is why months of successful renders
never showed it, and it bites hardest exactly where nobody is watching.

**The swallowed reason.** ffmpeg's stderr was inherited, and the progress line
is written with a carriage return and no newline. So ffmpeg's complaint landed
in the middle of that line and the next tick wrote over it. The reason was
always printed and never readable.

Its stderr is now drained on a thread of its own — it has to be read
continuously, because an ffmpeg blocked writing into a full pipe never exits —
and what it said is folded into our own error:

```
dossier: ffmpeg stopped after 1 frames: Broken pipe (os error 32)
   ffmpeg said: Error opening output …: No such file or directory
```

A signal with nothing said gets named for what it usually is:

```
ffmpeg exited with signal: 9 and said nothing. If that is a signal,
the machine most likely ran out of memory or disk.
```


## `-shortest` was cutting the fail tail off

A render on a server stopped at 6780 frames of 6849 with
`Broken pipe (os error 32)` and ffmpeg saying nothing at all. It read like a
dying encoder on a small machine, and the machine was checked for it: `/tmp`
was ordinary disk, not tmpfs; 1341MB of memory free; no OOM kill in the log.
None of it was the cause.

The cause is one flag and one arithmetic:

```rust
// The music outlasts the clip whenever only part of a map is rendered.
command.arg("-shortest");
```

True, and true in one direction only. `-shortest` ends the output with
whichever input runs out first, which is what is wanted when a slice of a long
map is rendered. On a **play that fails near the end of its song** it is
exactly backwards: the fail tail runs 3.72 seconds past the last judgement —
2500ms of movement, 220ms of clearing, and 1000ms of deliberate silence — and
the music underneath it has already ended. ffmpeg closed the pipe on time and
the renderer, still holding 69 frames, reported a broken pipe.

The missing 1.15 seconds were the silence. The engine was writing frames of a
held black screen with no audio left to accompany them.

`apad` on the end of every audio chain makes the audio endless, so `-shortest`
now always terminates on the video. Both halves are needed and each covers the
other's case; the test asserts every path ends in `apad`, including the plain
one, because the plain one is what runs for every render that is not of a
failed play.

Worth naming the shape of this. The clue that pointed at the machine — an
encoder that died silently — was the clue that pointed away from the truth. It
did not die. It finished, correctly, on the instruction it was given, and the
only thing that had gone wrong was that the instruction was written for the
opposite case.


## stable's ScoreV2 judges a slider twice and keeps the worse verdict

The largest single row in the corpus, and it had been sitting there as a scoring
problem when it was a judgement one. One replay, `NFHDV2`, 759 objects:

```
         ours    replay
  300     621       595
  100      85        98
   50       8        16
 miss      45        50
```

Fifty-two units of count error out of two hundred and sixty — a fifth of the
whole corpus on one file. The shape says the rule rather than the arithmetic:
the four differences sum to zero, so the objects are all accounted for and only
their grades are wrong, and every one of them is wrong in the generous
direction.

**The first half.** Under ScoreV2 a slider is worth what its head was worth,
read off the ordinary hit windows — the thing lazer does by default and
`NoSliderHeadAccuracy` restores. The engine already had that switch, and
turning it on for a ScoreV2 replay would have been wrong: `whole_sliders` also
carries lazer's handover from the head and lazer's 36ms window on the tail, and
stable has neither under any mod. ScoreV2 is a scoring mod; it does not touch
the follow circle. So the flag was split in two — `whole_sliders` for how a
slider is *tracked*, `head_carries_verdict` for what it is *worth* — and only
the second moves.

That fixed the 50s and the misses outright, 8 → 1 and 5 → 1, and left the
300/100 boundary 21 out in a perfectly balanced pair: twenty-one sliders graded
300 against the replay's 100, and no other column moving.

**The second half.** All twenty-one had dropped their tail. lazer can afford to
ignore that — its ticks and tails are judgements in their own right, counted
separately, so losing one cannot reach back and spoil the head's 300. stable
under ScoreV2 has nowhere to put them: the header carries four numbers and a
slider is one object. Both facts have to land on that one verdict, so the
verdict is **the worse of the two** — the head's window and the fraction of
pieces caught. A perfect head on a slider that let go of its tail is a 100.

```rust
from_head.max(slider_judgement(parts_hit, parts_total))
```

| | before | after |
|---|---|---|
| this replay's count error | 52 | **8** |
| corpus total | 260 | **216** |

Nothing else regressed — the manifest's per-replay check reported `0 worse`
across all 128 rows, which is the first time that check has earned its keep on
a change this broad.

What is *not* done is ScoreV2's own arithmetic, the millionth-scale formula.
stable is closed and no reimplementation to hand states it, so the score for
this replay stays `comparable() == false`: a ScoreV1 total, right to draw and
wrong to compare. The 52 units were never the formula's — they were the
judgement's, and the mod's name had been hiding that.


## Every disagreement left in the corpus is accounted for

The milestone this was working towards was never "no disagreements" — it was
"every replay is either exact or has a named reason". That is now true, and the
naming turned out to be one reason for almost all of them.

Two facts, measured across the whole corpus.

**Nothing is lost or invented.** All 48 disagreeing replays have differences
that sum to exactly zero across the four counts. Not most of them — all of
them. Every object the game judged, we judge; we sometimes grade it differently.
The object model, the stacking, the part counting and the slider decomposition
are right everywhere the corpus can see, and the engine has never once produced
a map with the wrong number of things in it.

**What is left lives on the window boundaries.** Counting the hits that land
within two milliseconds of a hit window edge:

| | replays | hits near a boundary, mean |
|---|---|---|
| exact | 87 | **15.4** |
| disagreeing | 48 | **51.1** |

A replay with few hits near an edge agrees with us. One with many disagrees —
and **47 of the 48 disagree by no more than the number of hits sitting on those
edges.** The one exception has zero hits near a boundary and is the Kona-Chan
repeat below.

This is a bound rather than a proof: it does not show that any particular object
was decided by a boundary, only that no *other* cause is needed to account for
the size of what is left. That is the same test `_explain_tails` was written to
apply, turned on the corpus as a whole.

**Why it is a floor and not a bug.** The direction splits: 27 replays grade
more strictly than the game, 22 more generously, one even. A missing rule or an
off-by-one produces error in *one* direction — that is how the note lock, the
tail window and ScoreV2 were all found. Error that splits near-evenly around a
threshold is rounding, and the rounding is not ours to fix: a replay records
frame times as whole milliseconds, while stable judged them against an audio
clock that is not integral. A hit whose recorded error is exactly 30ms on a 30ms
window may have been 29.6ms to the game. The replay does not carry the digit
that would settle it.

### The one that is not the boundary

`solo-replay-osu_6097` — Kona-Chan, HDHRDTFL, 55 objects, a 100% play. We break
combo once and lose forty:

```
#46 at 55539ms, 485ms long over 3 slide(s) — lost repeat
   follow circle 23px
   repeat at 55862ms — ball (97,289), cursor 23.3px away
```

Three tenths of a pixel outside a twenty-three pixel follow circle. HR and a
high CS make this the smallest follow circle in the corpus, which is why it is
the only replay where sub-pixel disagreement in the ball's position along the
path reaches a verdict at all. The candidates are the path length arithmetic and
the repeat's exact instant; neither is visible at any other circle size, so this
replay is the whole experiment.


## The padding had to say how long

The fix above traded one failure for another, and the diagnostics built the day
before caught it in one line:

```
dossier: ffmpeg stopped after 1439 frames: Broken pipe (os error 32)
   ffmpeg exited with exit status: 234
   ffmpeg said: non monotonically increasing dts to muxer in stream 1:
                9223372036854775807 >= 1046528
```

`9223372036854775807` is `i64::MAX` — ffmpeg's `AV_NOPTS_VALUE`, the sentinel
for "this packet has no timestamp". A bare `apad` is an endless stream, and an
endless stream eventually hands the mp4 muxer a frame with nothing to sequence
it by. The muxer is right to refuse it.

`apad=whole_dur=<video seconds>` instead. The length was known all along —
`Plan::video_seconds`, the number the render is already sized by — so the pad
had only to be told it. `apad` never truncates, so music that outlasts the video
is still cut by `-shortest`, and both directions stay covered.

The replay that started this renders end to end now: 6849 frames, the same 6849
it died 69 frames short of, with a 114.13s picture and 114.14s of sound.

The test that guards it asserts the pad is present *and* bounded — `apad[` with
no length is now a failure in its own right — because the unbounded form fixed
the visible bug and introduced an invisible one, and only the second half of
that lesson is worth encoding.


## A cut audio file was taking the hit sounds with it

The muxer error came back on a different replay, and chasing it found a fault
that had nothing to do with muxing.

```
[m][h:a]amix=inputs=2:duration=first:normalize=0[mix]
```

The music is the mix's **first** input, so `duration=first` ended the mix when
the music ended. That is harmless while a map's audio outlasts its gameplay,
which is nearly always. The replay that failed was on
`10 Things I Hate About You (Sped Up & Cut Ver.)`: a cut audio file, shortened
further by DoubleTime, running out at 23.7 seconds of a 61-second render.

So for the last thirty-six seconds the mix was over. Every hit sound in it was
discarded — silently, with a perfectly valid video to show for it. Nobody would
have reported that as a bug; they would have reported the map as quiet.

It was also the muxer error. `apad` was being asked to invent thirty-six seconds
of silence downstream of a stream that had already ended, which is where
`AV_NOPTS_VALUE` came from.

One change fixes both: pad the **music** up to the video's length *before* the
mix, and mix on `duration=longest`.

```
[1:a]atempo=1.500000,apad=whole_dur=61.300[m];
[m][2:a]amix=inputs=2:duration=longest:normalize=0[mix]
```

The pad goes after the stretch — stretching afterwards would scale the silence
along with the music — and after that everything downstream is handed a stream
that lasts as long as the picture, which is what both the mix and the muxer had
been assuming all along.

The trailing pad stays as well. It costs nothing and it is the only thing
standing between a future filter that shortens the chain and this same evening.


## `atempo` then `adelay` is a packet with no timestamp

The muxer error came back after the padding was bounded, on a different replay,
and this time it was not the padding at all. It had been there from the start.

Bisecting the filter graph by hand against the real inputs — a `color` source
standing in for the video, so the whole thing runs in a second — settled it in
five runs:

| chain | |
|---|---|
| music alone, whole chain | fine |
| hit sounds alone | fine |
| `amix` of the two, unfiltered | fine |
| `volume` → mix, `atempo` → mix, `adelay` → mix | fine |
| **`atempo,adelay` → mix** | **fails** |

Either filter is fine alone. The mix is fine. Only that pair, and only once its
output meets another stream: ffmpeg then hands the muxer a packet stamped
`AV_NOPTS_VALUE`, and mp4 refuses it. Which is why this only ever appeared on a
rate mod applied to a render that starts before the song does — DoubleTime on a
replay whose lead-in is being drawn.

Two fixes worked: `asetpts=N/SR/TB` on the end of the chain, and swapping the
two filters. The swap is the one taken. It removes the pairing instead of
regenerating timestamps over the top of it, and the arithmetic comes out exact:

```
adelay=1500:all=1,atempo=1.500000
```

The delay has to be restated in the music's own time, since `atempo` is about to
divide it — and `delay_seconds × tempo` is exactly `-from_ms`, the lead-in it was
derived from. The round trip closing on itself is the check that the reordering
is honest rather than merely quiet: a 1500ms lead-in is 1000ms of video under
DoubleTime, and 1000 × 1.5 is 1500 again.

Two tests hold it: the order with the scaled delay, and the case where no
`atempo` is emitted at all — at rate 1.0, or at a rate `atempo` will not do in
one pass — where scaling the delay would push the music late with nothing left
to bring it back.

### What this cost, and what it bought

Three separate faults wore the same error message, and two of them were mine:

1. `-shortest` cutting the fail tail off a play that died near the end of its
   song. Real, fixed.
2. `apad` with no length, added by that fix. Real, fixed.
3. `atempo,adelay` into a mix. **Present all along**, and the one the reports
   were actually about.

Chasing 1 and 2 was not wasted — both were genuine and both would have surfaced
eventually — but the lesson is about verification. Fix 2 was declared verified
on a render of a *window* of the replay, twenty seconds long, which never
reached the music's end. A window is not the play, and a render that completes
is not a render that completes for the right reason. The engine was also rebuilt
only after the test run, so the binary being measured was the one from before
the change: `cargo test` builds the test harness, not the release binary, and
the two are not the same artefact.


## Kona-Chan is the boundary again, in space instead of time

The one replay the window-boundary bound could not account for turns out to be
the same fault wearing different units.

`solo-replay-osu_6097` is a 100% play — 55 objects, 55 threehundreds, a full
combo of 220. We break combo once, on the second repeat of slider #46, and lose
forty. CS 10 under HardRock makes the hit circle 9.60px and the follow circle
**23.05px**, the smallest in the corpus, which is why this replay is the only
place the question comes up at all.

Reading the replay's own frames around the repeat:

| | cursor | distance to the ball |
|---|---|---|
| frame 55847 | (119.11, 287.11) | **22.19px — inside** |
| repeat 55862.3 | *not recorded* | 23.27px interpolated |
| frame 55866 | (120.44, 286.67) | 23.56px — outside |

The cursor crosses 23.05px at **55858.9ms**. The repeat falls 3.4ms after that
crossing, inside a 19ms gap between two recorded frames. Had stable's own update
landed anywhere in the first 11.9ms of that gap it would have credited the
repeat, and the replay says it did.

So the deciding fact — where the cursor was at 55862.3 — is not in the file.
Nineteen milliseconds of it are not in the file. This is the hit-window finding
in another dimension: there the replay's whole-millisecond frame times cannot
settle a hit sitting on a 30ms boundary; here its ~60Hz sampling cannot settle a
crossing of the follow circle.

### The experiment, and why it was reverted

The obvious reading — stop inventing positions, hold the last recorded frame —
was implemented and measured. It is worse.

| | 300 | combo |
|---|---|---|
| replay | 55 | 220 |
| interpolated (kept) | 54 | 180 |
| held to the last frame | **49** | **142** |

Holding fixes this repeat and drops five other parts of the same replay, because
a held sample is systematically staler than the ball it is compared against.
Across the corpus it moved nothing: the total stayed at 216 and no replay got
worse, which is its own kind of answer — a change that is principled, costs
nothing, and buys nothing is a change that is not describing the mechanism.

Real stable interpolates too, but at *its* update times, which are not the
replay's frame times and are not recorded anywhere. Two natural readings were
tried and neither reproduces it, which is the evidence that the third thing —
the update cadence — is what mattered and what is missing.

`FOLLOW_CIRCLE_SCALE` was left alone deliberately. Widening it to 2.43 would
pass this replay; it would also be a constant fitted to one object on one map,
and the docs above already record what that costs.


## stable's ScoreV2, from danser's source

The last replay whose score could not be compared at all. `comparable` was a
flag that said "this is scored by a formula we have not implemented", and one
replay in the corpus carried it.

`scoreV2Processor` is three terms and no map difficulty:

```go
s.score = int64(math.Round((s.comboPart/s.comboPartMax*700000 +
    math.Pow(float64(acc), 10)*(float64(s.hits)/float64(s.maxHits))*300000 +
    s.bonus) * s.modMultiplier))
```

Seven hundred thousand for combo, three hundred thousand for accuracy raised to
the tenth, bonus on top. ScoreV1's difficulty multiplier — the whole of its
spread between maps — is simply absent, which is the point of the mod.

Two details that are not decoration. The combo term reads the combo **after**
the hit increments it, where ScoreV1 reads it before. And `acc` is computed in
`float32` before the tenth power, so the width of the float decides the last
digits of the score.

### Three multipliers, one of them by half

```go
if mods&NoFail > 0 && mods&ScoreV2 == 0 { multiplier *= 0.5 }
if mods&HardRock > 0 { if mods&ScoreV2 > 0 { *= 1.10 } else { *= 1.06 } }
if mods&DoubleTime > 0 { if mods&ScoreV2 > 0 { *= 1.20 } else { *= 1.12 } }
```

**Under ScoreV2, NoFail costs nothing.** The replay is `NFHDV2`, so missing that
single condition put the score at exactly half — which reads like a broken
formula rather than a missing `if`, and cost a detour into the shape of the sum
before the multiplier was checked.

| | ours | theirs | |
|---|---|---|---|
| before | 58 223 | 114 086 | −48.97% |
| with the ScoreV2 multipliers | 116 447 | 114 086 | **+2.07%** |

### The residual is not the formula

Our accuracy on this replay is 83.22% against the game's 83.05% — the eight
counts still in dispute. At the tenth power:

```
(0.8322 / 0.8305)^10 = 1.0207
```

**+2.07%**, to the last digit measured. The whole of the remaining error is the
judgement disagreement amplified by the exponent, and none of it is the
arithmetic. Fix the eight counts and the score follows without touching this
code.

### Where this departs from the source, and why

`ModifyResult` decides a ScoreV2 slider from its pieces *and* its head:

```go
if result&Hit300 > 0 && startResult&Hit300 > 0 { return Hit300 }
else if result&(Hit300|Hit100) > 0 && startResult&(Hit300|Hit100) > 0 { return Hit100 }
else if result != Miss { return Hit50 }
```

The first two branches are taken as written. The third is not. Read literally it
gives a 50 to a slider whose head was missed and whose body was then tracked —
`result` is the pieces' verdict, high, and only `startResult` is the miss.
Implemented that way this replay went from eight counts out to sixteen, turning
five of the game's misses into fifties. The departure is one condition: a missed
head takes the slider with it. Most likely danser's `result` is already a miss
there and the branch is unreachable rather than wrong; our pieces' verdict is
assembled differently and reaches it.

Also tried and reverted: dropping the slider head from the combo sum, since
danser's `Init` builds its maximum without one. That took the score from +2.07%
to +23.28%, so whatever `Init` is doing, it is not a description of what the
play emits.

| | before | after |
|---|---|---|
| scores comparable | 130 of 131 | **131 of 131** |
| within 0.5% | 113 | 113 |


## Spinner turns: health, points and bonus

A spinner produced exactly one event — its 300/100/50 — and everything that
happens *during* one was missing: the health it pays as it turns, the hundred
every second turn is worth, and the bonus past the requirement.

The rule is stingier than it looks from the game:

```go
if scoringRotationCount > requirement+3 && (scoringRotationCount-(requirement+3))%2 == 0 {
    SpinnerBonus      // 1100 under ScoreV1, 500 under ScoreV2
} else if scoringRotationCount > 1 && scoringRotationCount%2 == 0 {
    SpinnerPoints     // 100
} else if scoringRotationCount > 1 {
    SpinnerSpin       // nothing
}
```

Only every **second** turn pays its hundred, the first pays nothing, and the
bonus does not start when the requirement is met — it waits three turns more and
then also arrives every second turn. Paying every turn, which is what this had
first, put seventeen corpus replays over their pinned score.

| | replays over their pinned score | within 0.5% |
|---|---|---|
| every turn pays, bonus from the requirement | 17 | 113 |
| **as quoted** | **6** | **115** |
| every turn pays, bonus as quoted | 8 | 115 |

The third row is there because danser's counter is ambiguous about its unit:
`rotationCountF` accumulates `|addition| / π`, which is half-turns, while
`requirement` is stated in whole spins. Read as half-turns the rule would pay a
hundred per full turn — measured, and worse. So it is taken at face value in
turns, which is both what it reads like and what the replays agree with.

Health pays per turn up to the requirement and nothing beyond it. Not because
osu! is known to stop, but because the calibration loop that solves for the
drain counts `required_spins` and no more: paying for the extra turns would make
the model and the play disagree about the same spinner, and the model is what
the drain is solved against.

The six that remain over are out by between 0.02 and 0.24 percentage points,
which on these maps is one spinner's worth of turns counted a little
differently — the sweep here is over the replay's own frames, and stable's is
over its update loop.

### What the spinner shows, and one thing it stopped doing

**RPM**, to the right of the centre. danser carries a decaying average:

```go
decay1 := math.Pow(0.9, timeDiff/FrameTime)
state.rpm = state.rpm*decay1 + (1.0-decay1)*(math.Abs(state.currentVelocity)*1000)/(math.Pi*2)*60
```

That needs per-frame state a live game has and a renderer must not: any frame
here has to be drawable without the ones before it, which is what lets them be
drawn in parallel. A trailing window over the replay is the same quantity — turns
over time — read rather than accumulated, and at a fifth of a second it settles
about as fast as the decay does.

**The bonus total**, below the centre, is the one thing on screen that is an
event rather than a reading — so it is placed where the closing ring crosses it,
on purpose. Each award arrives lit and oversized, shrinks inward to its resting
size and fades toward grey, then holds the running total until the next one
lights it again. The number is its own history: a spinner still paying keeps
flashing, one that has stopped sits grey at whatever it reached.

The step is **a thousand**, not the eleven hundred the score gets.
`hitSpinner.Bonus(1000)` sits directly beside a `SpinnerBonus` worth 1100 —
osu! displays and pays different figures, and putting the score's number on the
screen would have been plausible and wrong.

**And the turns make no sound.** Adding them to the judge for the score's sake
gave each one a hit sound by default, several a second, which turned every
spinner into a machine gun. `voice_for` now names them and returns nothing — the
lesson being that a new judgement part is picked up by everything downstream that
matches on parts, including the parts of the program nobody was thinking about.


## The scoreboard is part of the play, not a caption

Rivals down the left, the way osu! does it, and for the same reason: the left of
a playfield is its emptiest part, and a list that has to stay readable for four
minutes cannot sit where the notes are.

It is drawn in the engine rather than pasted over the video afterwards because it
**moves**. The player's row carries the score the engine is already computing
frame by frame, the list is sorted at every frame, and a row that passes another
passes it on screen at the moment it actually does. A scoreboard composited on
afterwards is a caption; this one is part of the play.

Four decisions worth keeping:

**Rows never animate between positions.** A row that slid would be prettier and
would also mean a frame could not be drawn without knowing where the rows were a
moment ago — and every frame here has to stand alone, or they cannot be drawn in
parallel. The same constraint that shaped the RPM window shapes this.

**The player's own row is never read from the file.** It is computed. A supplied
copy would sit beside the live one and disagree with it, which is worse than not
having it — so a row whose name matches the player is dropped on parse.

**A tie leaves the player behind.** Level is the moment before passing, not
after. Showing them already ahead reads as a place they have not earned.

**Tab-separated, and a bad row is skipped rather than fatal.** A player name can
contain spaces, commas and almost anything else; a format a legal username can
break is a format that will be broken. And refusing to render four minutes of
video over one malformed line would be the wrong trade — the row's absence is
visible in the list itself.

The engine takes the rivals and neither fetches nor validates them:
`--leaderboard <tsv>`, one `name<TAB>score[<TAB>accuracy]` a line. Who belongs
in a chat and what they scored is the bot's knowledge, and the renderer having an
opinion about it would put two answers to that question in the repository.


## NoFail takes the bar and the warning with it

A replay set with NoFail draws neither the health bar nor the red creeping in
from the edges.

The warning is the clear case. Red at the edges means *this is about to end*, and
under NoFail it never was. A warning that cannot come true is worse than no
warning at all, because a viewer who learns to discount it discounts the real one
too — and the real one is the whole reason the edges exist.

The bar going with it is the deliberate part, and it is worth being honest about:
the bar is **not** meaningless under NoFail. The drain still runs and the bar
still moves. But everything the bar is *for* is gone. Its job on screen is to say
how close the play is to being over, and on a play that cannot be over it reads
as a threat that is not there. Two lines of information where one of them is a
lie about the stakes is worse than one line.

Which is also why the two are one check rather than two: they are the same claim
at two volumes, and hiding one while drawing the other would leave the frame
saying half of it.


## The scoreboard's currency has to match the replay

osu!'s API answers with two scores and they are three orders of magnitude apart.
`total_score` is lazer's standardised million; `legacy_total_score` is the
original ScoreV1 total, which runs into the hundreds of millions.

The player's own row is computed by the engine in whichever arithmetic the replay
was recorded with. So a stable replay drawn against `total_score` rivals put the
player at forty million above a field of seven hundred thousand, and the board
said nothing at all except that its two columns disagreed about what a point is.

The field is now chosen by the replay's client, and a rival whose score cannot be
expressed in that currency is **left out rather than converted**. There is no
honest conversion: ScoreV1 depends on the map's difficulty multiplier and the
combo carried into every hit, and lazer's standardised score deliberately throws
both of those away. A row dropped for that reason is counted in the log.

It cost a shortcut, which is worth recording. `UserMapAttempt.score` holds
whatever the profile sync picked — the lazer total — so on a stable board the
local table cannot be used and those replays pay the full round of rate-limited
lookups. Correctness first; the sync could carry both fields later.

### And the layout follows the widest thing in it

Two rows of two columns per card: the rank and name with the mods opposite, the
score with the accuracy opposite. Which fact goes where was decided by width
rather than by taste — a ScoreV1 total is eleven characters, and put beside an
accuracy the two collide on the first stable replay. They did.

Two more things the first attempt got wrong, both worth keeping written down: a
card painted in the background colour is invisible on a near-black field, so it
is lifted off it first; and a card sized from the text size instead of *for* the
text leaves the second line hanging below its own panel.


## The playfield sat too low, and the reason was the units

The field is 512×384 osu!pixels, fitted to 80% of the frame and centred. That
part was right. What was wrong was the shift below centre.

danser's `SetOsuViewport`, emulating stable:

```go
baseScale := float64(height) / OsuHeight
if OsuWidth/OsuHeight > float64(width)/float64(height) {
    baseScale = float64(width) / OsuWidth
}
scl := baseScale * 0.8 * scale
if osuOffset { shiftY = 8 }
camera.positionV = vector.NewVec2d(shiftX, shiftY).Scl(scl)
```

Eight **osu!pixels**, scaled with everything else. This engine had it as 2% of
the frame height — which is the same number at 16:9 and nothing like it anywhere
else:

| frame | danser | ours (before) | |
|---|---|---|---|
| 1920×1080 | 18.0px | 21.6px | +20% |
| 960×1080 | 12.0px | 21.6px | **+80%** |
| 1080×1920 | 13.5px | 38.4px | **+184%** |

The fit itself matches: `min` of the two scales is exactly danser's "by height
unless the frame is narrower than 4:3", and the horizontal centring is the same.
So the field was the right size in the right place horizontally and a few pixels
low — enough to notice beside danser at 16:9, and enough to be plainly wrong at
any other shape.

The lesson is the units, not the number. The field is measured in osu!pixels end
to end; its offset has to be as well, or the layout stops being a property of the
game and becomes a property of the window. The test pins it at three aspect
ratios, including a portrait one, because 16:9 alone cannot tell the two
formulations apart — which is why this survived as long as it did.


## The scoreboard, rebuilt

Five rows, read **upwards** to the leader, on rounded cards with each player's
avatar and profile cover.

The order is the point. A board with the leader on top is a table; one that
climbs to them is a story, and the player's row rising through it is the only
thing on screen that changes place. Five rows because that is enough to be a
standing and short enough to take in without reading — and the player's row is
always among them, even when they are nowhere near the top. A scoreboard that can
hide the play it belongs to is worse than a shorter one.

### The move is computed, never remembered

A row that changes place slides to its new one, shrinking and fading while it
travels so the place it left reads as vacated. That could not be done by
comparing against the previous frame: **every frame here has to be drawable
without the ones before it**, or they cannot be drawn in parallel — the same
constraint that shaped the RPM window and the fail animation.

It does not need to be. The score curve is known in full before a frame is drawn,
so the instant the player passed each rival is known too:

```rust
pub fn reached(&self, score: u64) -> f64
```

The most recent rival score below the current one gives the moment of the last
place change, and a frame works out its own animation from that. No history, no
shared state, no ordering between frames.

### Four things the first attempt got wrong

Each one is a rule worth keeping, not a slip:

**A card in the background's colour is invisible** on a near-black field. It has
to be lifted off the background before it is laid down.

**A card sized from the text size** rather than *for* the text leaves the second
line hanging below its own panel. The step is derived from the two baselines and
the descender.

**Two rounded rectangles side by side leave a notch** where they meet and read as
two cards. The left wash keeps the card's corners and squares off where the right
one takes over.

**A profile cover can be any brightness at all** — including white snow and a
bright sky — and dark text over one simply disappears. It looked exactly like the
line had been truncated: a bug report waiting to happen about something that was
never wrong. Both washes go harder when there is a cover behind them. The cover
is decoration; the numbers are the point, and the numbers win.

### And the pictures are PNGs from outside

Paths in the row, decoded once when the scene is built rather than per frame — a
row is drawn thousands of times over a render, and a decoder in the frame path is
a decoder that can fail halfway through a video.

PNG only, and that is deliberate: the engine has one image decoder and no
network. Converting whatever osu! serves is the bot's job, and the bot already has
an imaging library and already caches every avatar it has seen.


## During a spinner the error bar has nothing to say

So it gives its place up. The bottom of the frame carries `RPM: 384` for as long
as the spinner runs, and the bar comes back after.

The reason is not that the space is free. It is that the bar would be **wrong** —
there are no clicks during a spinner, so it would sit there showing the timing of
the last note before it for as long as the spinner lasts. A stale reading is
worse than an empty space and much worse than a live one, and it is worse
precisely because it looks like a current one.

The swap fades over a quarter of a second in each direction, and opens a little
before the spinner starts and shuts a little after it ends — so it has happened
by the time the ring appears and is undone by the time the next note is due. A
bar that vanishes and a number that appears on the same frame reads as a glitch;
one giving way to the other reads as the display changing its mind, which is
what it is doing.

The reading is taken from whichever spinner is *nearest* in time, not the first
one found. At the seam between two spinners the number being read has to be the
one on screen.


## Hidden was taking the spinner away

A spinner under Hidden drew nothing at all: ring gone, centre mark gone, a black
screen with a cursor circling in it for the length of the section.

The fade-out was being applied to spinners the way it is applied to notes. It
must not be. Hidden removes what you would otherwise **read ahead**; a spinner
has nothing to read ahead, because it is a thing you are already doing. osu!'s
own mod does not touch it — a spinner does not appear in the switch at all, the
same way the reverse arrow and the slider ball do not.

The `is_spinner()` in the condition is the whole fix. The test renders one long
spinner with and without the mod and holds the two frames to the same brightness,
because a fade that is 95% finished looks identical to one that never ran until
somebody watches a real replay.

### How it survived

It was found by looking at a frame. It had not been found earlier because four
"spinner clips" had been rendered and sent without ever being opened — and every
one of them was black, for a second reason: the spinner they were aimed at
belonged to a *different difficulty of the same set* than the replay was played
on. The difficulty was picked by scanning the `.osz` for any `.osu` with a
spinner in it, rather than by checking which one the replay's own hash names.

Two mistakes, one shape: a window was rendered, the render succeeded, and the
success was reported without looking at what came out. `--strict` on the corpus
exists because a number can be checked automatically. A frame cannot, and the
only way to check one is to open it.


## The scoreboard was showing a page from a different story

Three faults, reported together, with one of them explaining most of what the
other two looked like.

**Slots were places.** A row's position on screen was computed from its place in
the whole field. On a map forty-two people had played, the leader's slot was
forty-one steps down — three thousand pixels below the frame. The block appeared
to have slid off toward the combo counter because most of it had.

**The window was the top of the map, not the play.** A board that always shows
the best five says nothing about a play sitting forty-second; it is a page from
a different story. The window is now the player's own place and the places just
above it, so it starts at the bottom of the field and climbs with them. Near the
top there is nothing better left to show, so it fills downward instead — arriving
first and being shown alone would be the one moment on the whole board with
nothing to compare against.

The place printed on a row is still its place among **everybody**: a play sitting
forty-second reads "42", not "5".

**Only one row had a face.** `avatar_data` is written by the profile sync, and
only when the URL has *changed* — so a chat member whose profile has not been
synced since that caching was added has a perfectly good `avatar_url` and no
bytes at all. The board drew the sender's face and empty frames beside it, which
reads as the same picture on every row. The bytes are now fetched for the handful
of rows about to be drawn and written back, so it costs one download per player
ever rather than one per render.

And the pictures moved out of the render's temporary directory. The gathered
board is cached per map per chat and names its pictures by path — written beside
a render, they vanish with it, and every re-render would have drawn a board of
empty frames.

### The order, twice

Drawn worst-first — the leader at the top, the player climbing from below — then
inverted on request so the list read upwards to the leader, then inverted back
once it could be seen. Both readings of "start at the bottom and climb to the
top" are defensible on paper; only one of them survives being looked at, because
the eye starts at the top of a list and starting it on the row that matters least
buries the one that matters most.


## Three faults in the board, and what each one taught

**The movement had one shape where it needed three.** Everything slid. A row
arriving does not slide at all: it arrives at the *top* of the window, because
the best row is the one that changes when the player climbs, and there is nothing
above the board to slide in from. It grows into place instead.

A row leaving **does not travel at all**: it collapses where it stood, and the
row above slides into the gap.

Three shapes were tried, in this order, and the order is the point. Dropping it
off the bottom is tidy and says only that a row left. Flying it into the row that
overtook it says *who* took the place — better, and it turned out to be the same
movement, because the player sits at slot zero and the row it displaces at slot
one. Watching that one back is what produced the third: collapsing in place and
letting the gap be filled tells the same event in the order it happened, and it
keeps the eye on the gap, which is where the next row is arriving.

One thing carried over from the second attempt and is worth keeping whatever the
shape: a leaver has to remain *visible* while it goes. Fading it with the same
ease-out curve that moves it makes it gone before anything has happened, which is
why the second attempt looked identical to the first — the change was correct and
invisible.

Sliding all three would make the board look like a list being sorted. That is
what it is; it is not what it is *for*.

**Half of every row was a black rectangle.** Two flat washes, and the heavy one
had to be heavy enough for text over the worst cover a player might have —
ninety per cent of a near-black background, which is not "darker", it is
"absent". Bands were wrong twice over anyway: they also leave a seam where they
meet, so one card reads as two.

A gradient replaces them, with a knee at the point the words stop. It puts the
weight where the letters are and lets go of it after them, so the picture
survives the half of the row that has fewer of them.

**A friend's replay wore your face.** The player's own pictures were looked up by
the *sender's* Telegram id, which is correct exactly when somebody renders their
own play and wrong every other time. The row belongs to the play, and the play
names its own player — so the lookup is by the osu! name in the `.osr`.

Nothing is drawn when the bot does not know them. An empty frame is honest;
somebody else's photograph is not, and it is the kind of wrong that looks
deliberate.

### And it is smaller, twice over

The cards began sized so a ScoreV1 total and an accuracy could sit at opposite
ends of one line — a third of the frame wide for the sake of the gap in the
middle. Putting the numbers together on one line took it to 0.285 of the frame's
height; a second pass took it to 0.225, with the step and the text down to
match.

What sets the floor now is the second line: eleven digits, an accuracy and a mod
acronym. It is shrunk to fit rather than allowed past the card, so the width is a
choice about how small the numbers may get and not about whether they fit.
