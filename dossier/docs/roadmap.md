# Where the engine goes next

Written 2026-08-11, from the state the engine is actually in rather than from
where it was meant to be. Two sibling documents already say what was decided and
why — [`stable-fidelity.md`](stable-fidelity.md) for judgement,
[`exhibit.md`](exhibit.md) for selection — and this one only says what is left.

## Where it stands

Seven crates, about 26,000 lines, 501 tests.

| crate | lines | what it is |
|---|---:|---|
| `dossier-replay` | 1,342 | `.osr` parsing |
| `dossier-beatmap` | 1,556 | `.osu` parsing, slider geometry |
| `dossier-sim` | 5,559 | judgement, scoring, health — the part with a right answer |
| `dossier-render` | 6,336 | frames, skin, elements |
| `dossier-exhibit` | 2,002 | which seconds of a play are worth watching |
| `dossier-audio` | 1,175 | hit sounds |
| `dossier-cli` | 8,174 | the commands, video, reels, the skin exporter |

Judgement is at the milestone it was aimed at: every replay in the corpus is
either exact or has a named reason, and the remainder lives on the hit-window
boundaries. Rendering has a look of its own, a slow-motion pass with a camera,
and a skin osu! can wear.

## The one thing blocking everything measurable

**The corpus is 13 replays out of 134.** The manifest names them; the files live
outside the repository and did not survive the move to this machine.

This is first because it is not a feature — it is the instrument. Every claim in
`stable-fidelity.md` was established by measuring across the corpus, and with a
tenth of it present:

- no judgement change can be shown to be safe, only asserted;
- `exhibit --survey`, which exists precisely because selection has no ground
  truth, has nothing to average over;
- the one open fidelity question below cannot be answered at all.

Nothing else on this list is worth starting before the replays are back.

## Correctness

**The note lock on the 37% replay.** The single open question in
`stable-fidelity.md`: the lock suppresses roughly as many clicks as that player
genuinely missed, and it is unknown whether it is right or coincidentally
right. The measurement is stated there — object by object, do the refusals land
on the notes osu! scored as misses. Needs that replay, which is among the
missing.

**Hit-window boundaries.** What is left across the whole corpus is hits within
two milliseconds of a window edge. Not obviously fixable; worth re-measuring
once the corpus is whole, because the shape of the remainder is the clue to
whether one rule is off by a rounding or many are off by nothing.

## Features

**A skin's own screen.** The grid shows a thumbnail — a hit circle and a
cursor, and nothing else, because at that size every extra piece made the
thumbnails look more alike rather than less. Everything else a person wants to
know about a skin belongs behind a tap on it:

- who sent it, and when;
- how much it weighs;
- its author, when the skin says who — `skin.ini` has `Author`, and most fill
  it in;
- four pictures of it with the whole interface, which is where the score face,
  the judgements, the slider and the spinner get to be seen. Those are the
  parts a thumbnail cannot carry and the parts somebody choosing between two
  similar skins is actually comparing.

The first three are already in the store: `stamp_of` records the filename, the
count and the owner at import, and the folder's own size is a `stat`. The
pictures are the work — and they are the case where rendering real frames
rather than compositing elements is the right answer, since at full size a
frame shows exactly what a video will look like.


**Exhibit's remaining list.** Slow motion at the first mistake is built — the
picture, the hit sounds, the music and the camera all follow one schedule, and a
reel finds the moment itself — but it is **switched off for reels** as of the
alpha. It does not yet read as a deliberate effect, and a reel is the thing
somebody shows other people, so an effect that looks like a bug is worse there
than anywhere else in the renderer. `reel::SLOW_INTO_A_MISTAKE` is the one line
that gives it back; `--slow-at` still drives the same schedule by hand, which is
how the shape of the dip gets worked out. Two items remain from the original
list:

- *Text over the clips naming the reason.* The JSON has said why since the
  beginning; burning it into the frame was deliberately deferred until the
  selection was trusted. It now is.
- *Picking between several replays of the same map.* A different feature that
  happens to share the scorers.

**The background is off by default.** `--background` works and costs nothing per
frame; the bot does not pass it. Turning it on changes how every render looks,
which is a decision rather than a flag.

**The skin is partial by design.** The scorebar, the spinner's remaining layers
and the hit-result variants fall back to the game's own. The spinner is the
honest gap: the wiki gives neither sizes nor stacking order for the new style's
layers, and a guessed spinner looks worse than the default it replaces.

## Quality

**`classic` is not as classic as it claims.** It advertises itself as imitating
osu! and draws a flat slider body, where the game draws a gradient from a dark
rim to a lighter core — the very gradient the `1984` skin now has. A fidelity
gap in the skin that is *about* fidelity.

**Stringly-typed errors in the CLI.** Two dozen `Result<_, String>` in
`dossier-cli`, while `dossier-replay` and `dossier-beatmap` have had `thiserror`
types from the start. Hygiene, not a bug.

**Edition 2021 on a 1.97 toolchain.** 2024 is available and costs almost
nothing.

**`--strict` means two things.** Alone it is `judge`'s "fail on any mismatch";
with a number it is `corpus`'s ceiling. Documented, and still a flag whose
meaning depends on whether the next argument parses as an integer.

## Shape

**The engine and the bot.** The engine is a Rust workspace inside a Python
bot's repository, and the two are joined at exactly one seam: the bot runs
`dossier` as a subprocess and reads the event stream. That seam is now
deliberate — `--events` was built to make it one — which is what makes pulling
the engine out a real option rather than a wish. The choice between a separate
repository, a published crate, or a clean boundary inside this one is the next
thing to settle, and it is a distribution question rather than a code one.

**`main` is 183 commits behind.** Everything above happened on `dossier`. The
merge is not a technical risk — the branch is a fast-forward — but the README
on `main` still describes an engine of 232 tests, and merging without rewriting
it would publish a description of something that no longer exists.

## Order

1. **The replays.** Everything measurable waits on them.
2. **Merge to `main`**, with the README rewritten to match what the engine is.
3. **Decide the engine's home** — repository, crate, or boundary — and move it.
4. Then the open fidelity question, and the two Exhibit features, in that order:
   one is a question about whether the engine is right, and the other two are
   about what it shows.
