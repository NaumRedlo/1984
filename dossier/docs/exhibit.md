# Exhibit — the telling moments of a play

**Built** — `crates/dossier-exhibit` chooses, `dossier exhibit` prints or
renders. `--json` for the selection alone, `-o` for the reel.

This document was written before any of it existed and is kept as it was
written, with the places the implementation departed from it marked **[built]**.
Those departures are the useful part: each one is something that only became
visible once there was output to look at.

## What it is

Given a replay, produce a short video of the moments that actually say
something about the play, and say *why* each was chosen.

The sneak-peek reel assembled by hand was the crude version of this: score
windows by object density, take the densest, prefer one containing a break.
It worked, and its limits are the reason for doing this properly — density is
a property of the *map*, so it picks the same moments no matter who played it
or how. A play where someone chokes at 98% and a play where they FC it produce
the same reel.

## The name

**Exhibit.** In a dossier, an exhibit is the piece of evidence you actually put
in front of someone — the rest of the file is context. That is exactly this
feature's job, and it sits beside `judge`, `frame` and `video` without
explaining itself.

The command reads plainly: `dossier exhibit replay.osr`.

(The alternative worth naming is **Extract**, which is a verb and matches
`inspect` and `judge` grammatically. It loses the evidence sense.)

## The hard part, stated up front

Judgement can be checked: osu! wrote the score into the replay header, so the
engine is either right or wrong and the corpus says which. **Exhibit has no
such truth.** There is no header saying which six seconds were worth watching.

That changes the discipline rather than removing it:

- **Every clip carries its reason.** If the engine cannot say why a moment was
  chosen, it should not choose it. The reason ships in the output, not just in
  a comment.
- **Selection is deterministic.** The same replay gives the same clips, always.
  A feature that cannot be reproduced cannot be argued about.
- **The selection is inspectable without rendering.** `--json` prints the spans
  and reasons in milliseconds; that is the surface tests assert against and the
  surface a human reviews. Rendering is a separate, later step.
- **Tests pin behaviour, not taste.** "A choke is chosen over a quiet stretch"
  is testable. "This is the best clip" is not, and no test will claim it.

## The signals, all of them already computed

| Signal | Where it lives now |
|---|---|
| Kiai sections | `Timeline::timing` — every timing point carries `kiai` |
| Combo runs and where each broke | `GameState::combo_chains` |
| Every hit, miss and slider part with its time | `Judge::events` |
| Signed timing error per click | `Event::error_ms` |
| Refused clicks | `Judge::shakes` |
| Breaks | `Timeline::breaks` |
| Cursor position over time | `GameState::cursor_track` |
| Object times, kinds, sliders, spinners | `Timeline::objects` |

Nothing new has to be measured to start. That is the point of starting now
rather than after more of the engine exists.

## Scorers

Each is independent, named, and produces candidate spans with a score and a
reason. Adding one is adding a function, not editing a pile of conditions.

- **`kiai`** — the mapper's own mark for where the song peaks. The cheapest good
  signal there is, and the only one that knows what the music is doing.
- **`peak`** — the end of the longest combo run. Where the play was at its best.
- **`choke`** — a combo break that ended a long run, weighted by how long the
  run was and how close to the end of the map it happened. A break at 96% into
  a map nobody has FC'd is the most interesting thing in the whole replay.
- **`storm`** — local object density, sliders weighted, as the hand-rolled
  version did. A property of the map, kept for maps where the play is clean and
  nothing dramatic happens.
- **`precision`** — a run of clicks with unusually low absolute error. Says
  something a density score cannot: that this stretch was played *well*.
- **`scramble`** — the opposite: a cluster of misses and refused clicks. Where
  it went wrong, which is often what a player wants to see.

**[built — added]** A survey — `dossier exhibit --survey <replays...>` — which
is the instrument the rest of this section is written from. Selection has no
ground truth and never will, so what stands in for it is knowing what a change
did across a hundred replays rather than across the two somebody watched.
Scorers declare one of three facets for it: **map** (the same seconds for
everybody who played it), **hand** (how this player moved and clicked), **run**
(what became of the run — the only kind that can say a play went badly).

**[built — fixed]** The survey found an asymmetry that had been there from the
first version. A map-side scorer is graded against the same map's own busiest
window and some window always is one, so every map hands `storm` and `travel` a
free 1.0. A play-side scorer anchors at perfection — a full combo, a window
where nothing survived — and read the ratio straight, so the median play's
longest run scored 0.32 and lost to a map that merely existed. The weight table
said `choke > peak > travel > storm`; the effective order was the reverse.

Two changes, measured over the same 123 reels:

| | before | after |
|---|---:|---:|
| clips about the run | 19% | 33% |
| clips about the hand | 40% | 39% |
| clips about the map | 42% | 28% |
| reels with nothing about the run | 16% | 1% |

1. **The play-side ratios go through a saturating curve** with a stated
   half-point. A third of a map without breaking is not a third of an
   achievement, it is most of one — while a handful of notes is still nothing.
   Three misses among seventy read as a moment to anybody watching and read as
   0.04 to a plain ratio.
2. **The map facet decays as a whole.** `storm` and `travel` measure the same
   sections from two sides, and their picks landed within half a minute of each
   other 59 times over 123 reels, each at full price because neither had
   repeated *itself*. Sharing the decay took that to 38.

   The edges are exempt, because the discounts are all for repetition and a
   play has one beginning and one ending. Applying it to the opening deleted
   the opening: eight clips over 123 reels, punished for a dense section having
   been shown earlier, which is not another look at anything.

**[built — added]** Three more scorers, once there were reels to look at:

- **`finale`** — how the play ended, which is the one thing every viewer wants
  to know. Two endings share it because they answer the same question: a play
  that *died* ends at the instant the bar empties and that instant is the whole
  story of the run, and a play that finished ends on its result — worth
  watching land in proportion to how good it is. A 99.4% arriving is a payoff
  and a 68% is the map running out, so the second gets no clip at all. Sits
  second in the weight table, under `choke`.
- **`opening`** — how the play begins. A reel that starts two minutes in at a
  combo of nine hundred gives no sense of the play; the viewer joins a run
  already in progress with nothing to measure it against. Graded on what the
  map gives it to establish, on the same density scale `storm` uses, and last
  in the weight table: it fills a budget that outlasts the things worth
  watching and loses to every one of them.
- **`travel`** — how far the cursor actually had to move. The one signal
  `storm` cannot reach: a jump map is *sparse* — a handful of objects a second,
  every one of them across the playfield — so counting objects calls the
  hardest thing in the map a quiet stretch. Read off the replay's own frames
  rather than the object positions, so it is what the player did and not what
  the map asked for.

  Spinner sections are cut out of it, and that is not a detail. Two hundred
  revolutions covers more distance than any jump pattern in the map, so without
  it the scorer finds the one place in a play where the hand is doing the
  easiest thing it ever does and calls it the hardest movement in the play.

`peak`, `choke`, `precision` and `scramble` depend on the replay. `kiai` and
`storm` depend only on the map. A reel made of the last two alone is the reel
we already have.

## Selection

Scorers propose; selection disposes.

1. Each scorer emits candidates as `(from_ms, to_ms, score, reason)`.
2. Scores are normalised per scorer, so a scorer cannot win by using a bigger
   number — they are compared on rank within their own kind.

   **[built — changed]** Rank within a kind turned out to be the wrong
   normalisation, and the reason is worth keeping. Normalising a scorer against
   *its own best* means its best always scores exactly its weight, so every
   scorer that fired at all wins a clip and the reel is the weight table read
   aloud. A flawless play got a "choke" clip because one of the runs it broke
   was the longest of them.

   Instead each scorer answers in the same unit — a strength from 0 to 1, in
   **absolute** terms, with each scorer stating what its 1.0 means (`peak`: an
   FC; `choke`: a run that had two thirds of the map behind it; `precision`:
   shedding all of the player's own average error). A scorer with nothing to
   say now scores near zero and drops out on its own, and the weight table
   became a ceiling rather than a result.
3. Candidates are taken best-first under three constraints: a total budget
   (default 30s), no overlap, and no two clips from the same stretch of the map
   unless nothing else qualifies. A reel that is six views of one section is a
   worse reel than one that shows the shape of the play.

   **[built — added]** A fourth was needed, for the same reason as the third and
   not covered by it: no two clips from the same *scorer* unless nothing else
   qualifies. On a map of uniform streams the density scorer produces dozens of
   windows within a hair of each other, in different stretches, and filled the
   reel with three clips that each told the viewer nothing the first had not.

   Both are discounts rather than bans, which is what makes "unless nothing else
   qualifies" fall out with nothing to special-case. Overlap stayed a hard rule:
   it is structural, not editorial.
   **[built — changed twice]** The budget stopped being the thing that ends a
   reel. It was 30 seconds, then 60, and both were wrong in the same way: how
   long a reel *should* be is a property of the play, not of whoever asked for
   it. A clean run of a quiet map has three things worth showing and a disaster
   on a marathon has a dozen — a fixed length pads the first with seconds
   nobody wanted and cuts the second off mid-story.

   So selection stops at a **worth floor** instead: the score under which a
   moment is not worth the seconds it would cost, `0.25` by default. Read
   against the weight table that admits any scorer's first good showing, a
   second helping from a strong one, and refuses a third of anything weak. The
   budget survives as a ceiling of two minutes, which no measured replay has
   come near — it is a guard against a pathological map asking for an hour of
   rendering, not a setting.

   The floor is absolute rather than relative to the reel's own best clip, and
   the difference matters: judged against its own best, a play where everything
   was mediocre still fills a reel, because mediocre is all there is to compare
   against. "Is this worth six seconds of somebody's time" is an absolute
   question.

   Measured over seventy replays, reels came out between 19 seconds — a
   forty-four-second play with two things worth showing — and 76.

   The budget that remains is spent in seconds rather than counted in clips,
   because clips are no longer all one length. **The more important a
   moment, the longer its clip runs** — up to 1.75× the base. A reel that gives
   the map's busiest eight seconds exactly as long as the break that cost the
   play says the two matter equally, and length is the only thing a reel
   without narration has to say "this one" with. Length comes from the
   moment's own score, before the discounts for repetition and crowding: those
   say whether to take a clip, not how long it deserves to be.

4. Chosen clips are ordered by time, not by score. A highlight reel that jumps
   backwards through the map is disorienting.
5. Each clip is nudged to start on a beat, using the timing already carried for
   the break arrows.

   **[built — added]** A clip sitting against either end of the play is not
   snapped at all. The opening and the finale *are* the play's edges, and one
   clamped to an edge has already been put where it belongs — sliding it a
   hundredth of a second to please the metronome undoes the clamp. The finale
   stopped 25ms before the last note and no longer showed the play ending.

   **[built — added]** A scorer also says *where in the clip* its moment
   belongs, which turned out to be most of what separates a clip that reads from
   one that does not. A choke wants the break about two thirds through, so there
   is a run-up to watch and a moment of aftermath; a peak wants its run ending
   at the last frame, so the number climbs while you watch and stops. Centring
   everything makes the whole reel feel arbitrary.

## Output

```
dossier exhibit [OPTIONS] <replay.osr>
    --for <seconds>    total budget (default 30)
    --worth <0..1>     **[built — added]** the score under which a moment is
                       not worth its seconds (default 0.25). This, not --for,
                       is what decides how long a reel is.
    --clip <seconds>   length of one clip (default 6)
    --json             print the chosen spans and reasons, render nothing
    -o <path>          the video
```

**[built — changed]** `--for` and `--clip` are in **video** seconds, and spans
come back in **map** milliseconds. Under DoubleTime those are not the same
second: six seconds of watching is nine seconds of map, and `dossier video`
computes its length as `(to - from) / rate`. So a budget of thirty means thirty
seconds of somebody watching, whatever the mods, and a span can still be pasted
straight into `--from`/`--to`.

`--json` first and `-o` second, deliberately: the selection is the feature and
the video is a consequence of it. Everything that can go wrong with selection
can be seen without waiting for an encode.

Stitching moves into the engine from the shell script that does it now —
crossfades between clips, and a fade from and to black. Six songs cut together
need the crossfade; hard cuts on the audio are unpleasant in a way hard cuts on
the video are not.

**[built — changed]** The video crossfades too, and not for its own sake: once
the audio overlaps by four tenths of a second at every join, the video has to
overlap by the same amount or the two drift apart by one fade per cut. By the
fifth clip that is over a second of the wrong sound under the right picture.

**[built]** Each clip is rendered by the ordinary `video` path and the clips are
then cut together in a second ffmpeg pass. Drawing straight into one long stream
would encode once instead of twice, and the audio is what rules it out: each
clip needs its own slice of the song, seeked and rate-adjusted and faded into
the next, and the existing audio path is built around one span — and is the part
of this program that has been wrong the most times. The second encode is thirty
seconds of video against the minutes spent drawing it.

## Not in the first version

- **Slow motion at the first mistake.** The shape asked for: as the reel
  approaches the first miss or slider break, ramp the speed down, then rewind
  and replay the moment slowly, then return to normal. It is the most valuable
  thing left on this list and the least like anything built so far.

  Three problems, in order of difficulty. The audio has to stretch with the
  picture or be dropped, and `atempo` ramps are not a thing ffmpeg does — the
  fail wind-down already had to be cut into ten fixed steps for exactly this
  reason, which is the shape the answer will take. The rewind means the same
  map time appears twice in one clip, and every timing assumption downstream —
  `Plan::map_time_of`, the audio seek, the hit-sound track — is that video time
  and map time differ by a constant rate. And the frames drawn during the slow
  section cost real time per second of video in proportion to how far the speed
  drops, so a quarter-speed second is four seconds of drawing.

  None of that is a reason not to build it. It is a reason to build it after
  the selection is trusted, and to expect it to change `Plan` rather than to
  sit beside it.
- Text over the clips naming the reason. The JSON says it; burning it into the
  frame is a design decision that should wait until the selection is trusted.
- Picking between several replays of the same map. A different feature that
  happens to share the scorers.
