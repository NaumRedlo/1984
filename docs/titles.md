# Titles

A title is a code in `utils/titles.py` (`TITLE_REGISTRY`) with a tier, a target and
words in two languages. Whether a player has it is decided by a rule that reads
what the bot knows about the player; once unlocked a title stays unlocked unless a
rule changes and `scripts/recheck_titles.py` is run on purpose.

## Tiers

Common, Uncommon, Rare, Epic, Legendary, Mythic and Anomaly. Anomaly holds the
coincidences and the oddities: the same score on two maps, a 1984x combo, 99.99%
without an SS, and the titles that used to be Secret. Those six keep their
riddles: a hidden title shows `???` and its hint until someone has earned it
(`TitleDef.hidden`, sent to the application as `hidden`).

## Where a rule reads from

`utils/title_history.py` loads one `History` of a player per refresh: the
attempts the bot saw (with their time), the older attempts without a time, the
best scores, the sessions Witness told and the player's time zone. A rule in
`utils/title_rules.py` is a function of that history. Counters kept on the
player (level, streaks, weekly plays, comparisons) and the chat leaderboard
(Archivist, Big Brother) are read in `utils/title_progress.py`.

- A play counts toward skill titles (passes, grades, FCs, runs) only when none of
  its mods is Relax, Autopilot, Auto, Cinema or Target Practice. Titles about
  volume (plays in a day, maps in a session, fails) count every attempt.
- Stars are the mod-adjusted ones when the bot has them (`eff_sr`), otherwise the
  map's own. BPM and length are taken as played (DT and HT change them).
- A run ("5 FC in a row") is taken among the plays that could belong to it
  (5* or harder, ranked, and so on): a play of another kind in between does not
  break it, a play of its own kind that fails the test does.
- A session is a stretch of attempts with no gap over 30 minutes, and it never
  crosses the border of a Witness session.
- A day is the player's own day in their own time zone, UTC when it is not known.

## What Witness tells

The application tells `POST /render/me/session` the device's time zone and, for
every game session it saw, when it began and ended, how many seconds the
player spent in a play and how many plays it saw. The row is kept once per
start and only grows. Titles use it for the player's local day (No Warm-Up,
Perfect Week, Daily Report, Clockwork), to cut attempts into sessions where the
client was closed, and for the playtime of a day: the larger of what the attempts
suggest and what Witness counted.

Plays Witness saw are not read by titles; they enter the bot's tables only when
osu! confirms them.

## Changing a rule

Add the rule and its test, then run

    DATABASE_URL=sqlite+aiosqlite:///copy-of-the-base.db PYTHONPATH=. venv/bin/python scripts/recheck_titles.py

on a copy of the base. It lists, per title, who holds it and who would lose it
under the current rules, and changes nothing until `--apply` is given.
