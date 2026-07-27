# 🎯 1984 | Global & Competitive Bot

<a href="https://www.codefactor.io/repository/github/naumredlo/1984"><img src="https://www.codefactor.io/repository/github/naumredlo/1984/badge" alt="CodeFactor" /></a> <a href="https://app.codacy.com/gh/NaumRedlo/1984/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade"><img src="https://app.codacy.com/project/badge/Grade/f89a6f6b9bac40f09b6fa29a577d202c"/></a>

A Telegram bot for osu! players, and the replay engine that grew out of it.

> *"You are no longer a participant — you have become part of the system itself."*

---

## What this is

Two things live here, and they are separable.

**The bot** tracks osu! accounts for a Telegram group: profile cards, recent
plays, leaderboards, a collection of titles, and weighted-pp top plays. It talks
to the osu! API v2, keeps its own database, and renders every card itself with
Pillow.

**Dossier** (`dossier/`) is an osu! replay engine written from scratch in Rust.
It reads a `.osr`, works out what the player actually hit, and draws the play
back as video. Nothing about it is a wrapper around anything: the replay parser,
the beatmap parser, the slider geometry, the judgement, the rasterised frames
and the hit sounds are all its own.

---

## Dossier

A replay file records where the cursor was and which buttons were down. It does
**not** record what each click hit — that has to be reconstructed, and doing so
is the difference between rendering a replay and animating a beatmap.

### What it models

| Piece | |
|---|---|
| **Judgement** | Notelock, hit windows, slider heads, ticks, reverses and tails, spinner rotations, combo and accuracy |
| **Tracking** | The follow circle only opens once a slide has started, and closes the moment the cursor leaves — as stable does it |
| **Rendering** | Playfield transform, combo colours and numbers, approach circles, reverse arrows, sliders that grow in and retract behind the ball, a HUD |
| **Audio** | The map's own track, plus hit sounds that follow the *judgement* — a missed note is audible by its silence |

### How it is checked

Synthetic tests only say the engine does what its author intended. The thing
that says it is *right* is the `.osr` header, because osu! wrote it: every
replay carries the score it earned, and the engine's totals are held up against
that figure. Where they disagree, the CLI is built to say **where** — which
slider part was dropped, how hits fall around a window edge, which object the
game's extra combo break must have landed on.

Every judgement rule that changed was measured over a corpus of real replays
before and after, and several plausible-sounding changes were reverted because
the corpus got worse. Six rendering optimisations were measured and rejected the
same way; the numbers are kept as `#[ignore]` benchmarks so nobody builds them
twice.

### CLI

```
dossier inspect [--json] <replay.osr>...     read the header alone, no map needed
dossier judge   [OPTIONS] <replay.osr>...    judge, and compare with the header
dossier sliders [OPTIONS] <replay.osr>...    break slider verdicts down by part
dossier errors  [OPTIONS] <replay.osr>...    how hits fall around the windows
dossier frame   [OPTIONS] --at <ms> <replay.osr>   one frame to PNG
dossier video   [OPTIONS] <replay.osr>       the whole play to MP4
dossier sounds  [OPTIONS] [-o kit.wav]       audition a hit-sound kit
```

Six crates: `dossier-replay`, `dossier-beatmap`, `dossier-sim`, `dossier-render`,
`dossier-audio`, `dossier-cli`. Video encoding shells out to `ffmpeg`; frames
are piped to it already converted to YUV, never touching the disk.

Rendering from the bot is gated to a separate `RENDER_TESTER_IDS` list — not to
admins. Running the bot and running an unfinished engine that shells out to a
native binary and fetches maps on demand are different levels of trust.

---

## The bot

| Feature | |
|---|---|
| 👤 **Profiles** | Auto-refreshing profile cards, recent plays, head-to-head comparison |
| 📊 **Leaderboards** | Six categories: pp, accuracy, play count, play time, ranked score, hits per play |
| 🏅 **Titles** | Achievements across seven rarities, with progress bars and an active title on the profile card |
| 📈 **Top plays** | Best scores by weighted pp — the same `0.95^(N-1)` curve osu! itself uses — with change tracking |

### Commands

Gameplay commands are deliberately short and take no slash. Case does not
matter.

| | |
|---|---|
| `start` | Greeting and quick start |
| `register <nickname>` / `reg` | Register |
| `link` / `relink` / `unlink` | osu! OAuth (unlink has a 30-day cooldown) |
| `rf` | Force a sync with the osu! API |
| `group` / `switch` | In DMs: choose which group's data to work with |
| `help` | Help menu |

| | |
|---|---|
| `pf` | Profile card |
| `rs` | Last played map |
| `cmp [username]` | Compare with another player |
| `lb` / `top` | Leaderboards |
| `lbm [id/url]` | A map's local leaderboard |
| `tpp` | Weighted top plays |
| `tt` | Title collection |
| `st` | Set the active title (`st off` to clear) |
| `sts` | Settings: account, language, active title |

---

## Running it

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python -m bot.main
```

Four environment variables are required, and the bot refuses to start without
them rather than failing later: `TELEGRAM_BOT_TOKEN`, `OSU_CLIENT_ID`,
`OSU_CLIENT_SECRET`, `OAUTH_ENCRYPTION_KEY`. A `.env` beside the project is
read automatically. Everything else has a default — see
[config/settings.py](config/settings.py), which documents each one where it is
defined.

Dossier is optional and built separately:

```bash
cd dossier && cargo build --release
```

`ffmpeg` has to be on the host for video. Judging and single frames do not need
it.

### Tests

```bash
./venv/bin/python -m pytest -q     # 775 tests
cd dossier && cargo test           # 232 tests
```

---

## Built with

| | |
|---|---|
| **Bot** | Python 3.12, aiogram 3.29, SQLAlchemy 2.0 (async) over SQLite, Pillow |
| **Engine** | Rust 2021, tiny-skia for rasterising, fontdue for glyphs, lzma-rs, ffmpeg for encoding |
| **API** | osu! API v2 |
| **Host** | Ubuntu Server 24.04 LTS |

---

## Licence

**GNU AGPL-3.0-only.** See [LICENSE](LICENSE).

The network clause is the reason for this one rather than a plain GPL: this
project is a *service*. Run a modified copy as your own bot and the people using
it are entitled to your changes — which is exactly the situation a distribution-
only copyleft leaves open.

### Third-party assets

The licence above covers this project's own code. It does **not** cover
everything under `assets/`, which is other people's work and is not the
project's to relicense:

| | |
|---|---|
| `assets/fonts/ProximaSoft-*` | Commercial typeface (Mark Simonson Studio) |
| `assets/fonts/TorusNotched-*` | osu!'s own typeface (ppy) |
| `assets/fonts/MPLUSRounded1c-*` | M PLUS Rounded 1c |
| `assets/hitsounds/1984/` | TickTok samples, included on their author's free licence — see [the note beside them](assets/hitsounds/README.md) |
| `assets/flags/` | Country flags, taken from the osu! framework repository |
| `assets/icons/` | Card icons from [Flaticon](https://www.flaticon.com/) |
| Dossier's arrows and spinner mark | Not files — paths, after work by [BizzBox](https://www.flaticon.com/authors/bizzbox) (reverse arrow), [Roundicons](https://www.flaticon.com/authors/roundicons) (break warning) and [Radhe Icon](https://www.flaticon.com/authors/radhe-icon) (spinner centre) on Flaticon |

Two of those carry conditions worth stating plainly rather than burying.

**Flaticon's free licence requires attribution** wherever the icons are used.
That is a term of use, not a courtesy, and naming it here is the minimum —
whether the rendered cards themselves need to carry a credit depends on how
they are distributed.

**The flags are recorded from memory** and have not been traced back to a
specific commit or licence file. osu!'s own framework is MIT, but a flag set
vendored into a repository is not automatically the repository's to relicense.
Treat this row as unverified until someone checks it.

If you fork this, check both before redistributing.

---

## Author

[@NaumRedlo](https://osu.ppy.sh/users/17397924) — Telegram `@NaumRedlo`

---

> *"Big Brother is watching you play."* 👁️
