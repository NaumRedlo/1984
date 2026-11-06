# Story

The story mode of Dossier lives on the server. The concept, tone and open
questions are in Dossier's `docs/story.md`; this file is how the server keeps it.

## Chapters

A chapter is one TOML file in `assets/story/`, read and checked once by
`services/story/chapters.py`. A chapter that does not pass the check stops the
load with the file name and the reason, so a broken chapter never reaches a
player.

```toml
id = "prologue"
start = "welcome"
title = { ru = "Пролог", en = "Prologue" }

[speakers.archivist]
ru = "Архивариус"
en = "Archivist"

[[steps]]
id = "welcome"
kind = "scene"
next = "check"
lines = [{ who = "archivist", ru = "…", en = "…" }]
```

A step is one of four kinds:

- `scene`: lines and `next`.
- `task`: `slot` (a duel slot), `below` (stars under the player's level in
  that slot, 0 to 3), an optional `goal` with `accuracy` (percent),
  `full_combo` and `mods`, and `then`.
- `choice`: 2 or 3 `options`, each with `id`, `flag`, `next` and words.
- `end`: lines and an optional `next_chapter`.

Every text has both `ru` and `en`. A line may carry `when`, one condition or a
list: a flag name, or `!name` for its absence. Flags are set by options
(`by_the_book`) and by tasks (`check.passed`, `check.fc`, `check.ss`). A bare
name is this chapter's flag; `prologue:kept_a_copy` reads another chapter's.

The check refuses: a step leading nowhere, a missing language, a speaker not
named, a slot that is not one, a loop, a step never reached from `start`, a
condition on a flag the chapter never sets, and a `next_chapter` that is not
written.

## Progress

`story_progress` keeps one row per player: chapter, step, flags, the map given
for the current task, and the finished chapters. A new player starts at
`prologue`.

A task's map is picked when the task opens, from the duel catalogue in the
task's slot, near the player's level there minus `below`. The level comes from
the duel portfolio, so it grows with the player's plays on catalogue maps. The
map is kept so it does not change in the middle of a try.

## Checking a task

`POST /render/story/check` reads the player's recent scores, and their scores on
the map, from the osu! API. A score counts when it is on the given map, ended
after the task opened, passed, and has none of RX, AP, AT, CN, TP. The goal's
accuracy, full combo and mods must hold. Memory reading and Witness are never
used to count a task.

After three tries without meeting the goal, the task moves to a map of the same
slot half a star lower and strictly easier than the first one. When no such map
exists the task stays as it is. A player may check once in 15 seconds.

## Routes

All take the device's token, like the other app routes, and `lang=ru|en`.

| Route | Body | Does |
|---|---|---|
| `GET /render/story` | | the current step, with lines filtered by flags |
| `POST /render/story/next` | `step` | moves past a scene |
| `POST /render/story/choose` | `step`, `option` | takes an option, sets its flag |
| `POST /render/story/check` | `step` | checks the task against osu! |
| `POST /render/story/finish` | `step` | closes the chapter, opens `next_chapter` |

`step` is the step the app is showing. When the server has moved on, it answers
409 `STEP_MOVED` instead of acting twice.
