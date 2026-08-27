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

**Dossier** is an osu! replay engine written from scratch in Rust. It reads a
`.osr`, works out what the player actually hit, and draws the play back as
video. Nothing about it is a wrapper around anything: the replay parser, the
beatmap parser, the slider geometry, the judgement, the rasterised frames and
the hit sounds are all its own. It has [its own
repository](https://github.com/NaumRedlo/Dossier); this bot is what asks it for
things.

---

## Dossier

The engine is its own repository — **[NaumRedlo/Dossier](https://github.com/NaumRedlo/Dossier)**
— and what it does is described there: the replay parser, the beatmap parser,
the slider geometry, the judgement, the rasterised frames and the hit sounds
are all written from scratch in Rust, and how each was checked against real
replays is the larger half of that README.

This bot is one of the two things that drive it. The other is the render
client, which lives in the same repository and does the work on somebody
else's machine.

What is here is the bot's side of it: the menu the settings are chosen in, the
mini app that shows them as a page, the scoreboard drawn down the left of a
render, the card a finished video is posted with, and the farm that decides
which machine does the rendering.

### Keeping the engine in step

The bot and every worker have to be on the same **build** of the engine, not
merely the same version: a worker running a different one is turned away,
because a stale binary renders something that looks right and is not.

One line decides which, and it is the line `pip` already reads:

```
dossier @ git+https://github.com/NaumRedlo/Dossier@v0.1.0#subdirectory=client
```

Everything else follows from it. `scripts/engine.py` reads that tag, downloads
the release built from it, checks it against the hash published beside it, and
moves a symlink:

```bash
./venv/bin/python scripts/engine.py
```

So `.env` names the link once and never again:

```
DOSSIER_BIN=/root/.dossier/engine/dossier
```

Run it from systemd as well as by hand, and the two cannot drift while nobody
is looking — a bot that starts is a bot whose engine matches it:

```
[Service]
ExecStartPre=/root/1984/venv/bin/python /root/1984/scripts/engine.py
```

Nothing is deleted: previous versions stay unpacked under `~/.dossier/engines`,
`--list` says what is there, and going back is the same link moved the other
way. `--force` fetches again regardless.

Updating, then, is two commands — bump the tag in `requirements.txt`, and:

```bash
./venv/bin/pip install -r requirements.txt && ./venv/bin/python scripts/engine.py
```

Maps and skins are shared through `BEATMAP_STORE_DIR` and `SKIN_STORE_DIR` and
belong to neither side.

Building from source instead of downloading is still a `git clone` and a
`cargo build --release` in the engine's repository, with `DOSSIER_BIN` pointed
at `target/release/dossier`. That is the only route on a machine no release is
built for — a Raspberry Pi, for one.


### Rendering somewhere else

A render is minutes of drawing and encoding, and the host this bot runs on has
one core. So each one is offered to a worker — any machine running the render
client from the [engine's repository](https://github.com/NaumRedlo/Dossier) —
and rendered on the bot's own host when none takes it. Falling back is the ordinary path, not the error
path: the worker is somebody's laptop and is allowed to be shut.

The worker pulls rather than listens, so nothing has to be reachable from
outside it and no address has to stay put. Almost nothing crosses the network
either: a replay names its map by MD5 and nothing else, so the worker fetches
the beatmap itself, and the job is an `.osr`, four settings and the scoreboard's
thumbnails. Only the finished video comes back.

How hard it works is the worker's own decision, made per job from the battery,
the energy mode, whether anyone is at the keyboard and whether the machine is
already hot — see `machine.py` in the engine's repository, which documents what
was measured and which two of those measurements changed the policy.

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

Everything render-related is behind `RENDER_TESTER_IDS`. A comma-separated
list of Telegram ids is an allowlist; `*` opens it to everybody; unset means
nobody, which is the right default for an engine still under construction.

Dossier is optional, cloned and built separately — see **Two folders, one
server** above.

`SHARED_REPLAY_DIR` is where replays go when their player ticked "send replay
data to the developer" in `sts`. Unset means nothing is kept whatever anybody
ticked. What is kept is the `.osr` and the engine's reading of it — exactly
what the consent text on that toggle says, and all that finding a judging
error needs.

`ffmpeg` has to be on the host for video. Judging and single frames do not need
it.

### Lending a machine to the farm

There is a Russian guide covering both halves — rendering a replay through
the bot, and running a worker — at [docs/guide.ru.html](docs/guide.ru.html).
That page is what to hand somebody rather than this section, and the bot serves
it at `/guide` on its own hostname: see `services/site.py`, and route the path
to the same upstream in Caddy alongside `/oauth/*` and `/render/*`.

Any machine with the engine built can render for the bot — a clone of the
[engine's repository](https://github.com/NaumRedlo/Dossier), not of this one.
It needs two lines in `~/.dossier/worker.env`:

```
RENDER_SERVER=https://your.host
RENDER_WORKER_TOKEN=the-same-string-the-bot-has
```

Two lines, not four: the bot has already looked the map up to draw the card, so
it sends what it found with the job and a worker needs no osu! account of its
own. That was the setup step most people got wrong.

Then ask whether the machine is ready. This answers every question at once —
the token, the engine, `ffmpeg`, what this machine would give right now, and
whether the bot agrees with its build — and it reaches the bot without claiming
anybody's replay:

```bash
python client/worker.py --check
```

When it says ready, run it:

```bash
python client/worker.py
```

`--polite` if somebody is using the machine, `--threads N` for a hard cap on
what the farm may take. Both, plus `RENDER_PAUSE` and `RENDER_HOURS=22-6`, can
also live in the config file, which is re-read every poll — a machine can be
handed back to its owner from a text editor, with nothing restarted.

`farm` in the bot lists every worker: what it is doing, what it is giving, and
whether its engine has drifted from the bot's. `--service` prints the launchd plist or systemd unit
that would keep it running, with the two commands to install it — it prints
rather than installs, and carries no token, so the output can be pasted
anywhere.

A worker whose engine differs from the bot's is turned away, because a stale
binary renders something that looks right and is not. It stands by and comes
back on its own once the build matches, so the fix is `git pull && cargo build
--release` in the engine's checkout and nothing else.

With `RENDER_WORKER_TOKEN` unset the endpoints are never registered and every
render happens on the bot's own host, as it did before there was a worker.

### The mini-app

`services/miniapp/auth.py` proves who opened the page from Telegram's signed
`initData`; `services/miniapp/api.py` reads and writes the same render settings
`sts` shows, reusing the bot's own parsers, ration and access check rather than
restating any of them. Both refuse to do anything without `TELEGRAM_BOT_TOKEN`.

The page itself is `docs/miniapp.html`, served at `/app` — settings on one
screen, and a grid of skins behind the row that names the current one, with the
client's Back Button to return. Skin thumbnails are drawn by
`services/dossier/preview.py` and served at `/app/preview/<name>.png` *without*
a signature, because a grid loads them with `<img src>` and that cannot carry
one; the name is looked up in the store's own listing, so nothing a request
says reaches a file. It holds no setting
name, label, bound or grouping of its own — all of that comes down from
`/app/api/settings`, so a setting renamed or re-bounded moves without the page
being touched. It takes its colours from Telegram's theme rather than bringing
its own, and saves through the client's Main Button.

Two tabs: settings, with the skin grid a tap behind the row that names the
current one, and the farm — who is out there, what they are doing, and what is
queued. A worker sends *why* it is not taking work as a word as well as a
sentence, so the app can say it in the reader's language; the sentence is the
fallback for a worker too old to send one.

Route `/app/*` to the same upstream in Caddy alongside `/oauth/*`, `/render/*`
and `/guide`.

### Tests

```bash
./venv/bin/python -m pytest -q     # 906 tests
cd dossier && cargo test           # 501 tests
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
| `assets/flags/` | Country flags, taken from the osu! framework repository |
| `assets/icons/` | Card icons from [Flaticon](https://www.flaticon.com/) |
| Dossier's arrow and spinner mark | Not files — paths, after work by [Roundicons](https://www.flaticon.com/authors/roundicons) (break warning) and [Radhe Icon](https://www.flaticon.com/authors/radhe-icon) (spinner centre) on Flaticon |

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
