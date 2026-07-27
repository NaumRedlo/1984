# Exhibit — the telling moments of a play

A design, not an implementation. Nothing below is built yet.

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

`peak`, `choke`, `precision` and `scramble` depend on the replay. `kiai` and
`storm` depend only on the map. A reel made of the last two alone is the reel
we already have.

## Selection

Scorers propose; selection disposes.

1. Each scorer emits candidates as `(from_ms, to_ms, score, reason)`.
2. Scores are normalised per scorer, so a scorer cannot win by using a bigger
   number — they are compared on rank within their own kind.
3. Candidates are taken best-first under three constraints: a total budget
   (default 30s), no overlap, and no two clips from the same stretch of the map
   unless nothing else qualifies. A reel that is six views of one section is a
   worse reel than one that shows the shape of the play.
4. Chosen clips are ordered by time, not by score. A highlight reel that jumps
   backwards through the map is disorienting.
5. Each clip is nudged to start on a beat, using the timing already carried for
   the break arrows.

## Output

```
dossier exhibit [OPTIONS] <replay.osr>
    --for <seconds>    total budget (default 30)
    --clip <seconds>   length of one clip (default 6)
    --json             print the chosen spans and reasons, render nothing
    -o <path>          the video
```

`--json` first and `-o` second, deliberately: the selection is the feature and
the video is a consequence of it. Everything that can go wrong with selection
can be seen without waiting for an encode.

Stitching moves into the engine from the shell script that does it now —
crossfades between clips, and a fade from and to black. Six songs cut together
need the crossfade; hard cuts on the audio are unpleasant in a way hard cuts on
the video are not.

## Not in the first version

- Slow motion on the hardest pattern. Tempting and a separate problem: it needs
  the audio to stretch with it or be dropped.
- Text over the clips naming the reason. The JSON says it; burning it into the
  frame is a design decision that should wait until the selection is trusted.
- Picking between several replays of the same map. A different feature that
  happens to share the scorers.
