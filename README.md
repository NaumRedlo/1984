# 🎯 1984 | Global & Competitive Bot

<a href="https://www.codefactor.io/repository/github/naumredlo/1984"><img src="https://www.codefactor.io/repository/github/naumredlo/1984/badge" alt="CodeFactor" /></a> <a href="https://app.codacy.com/gh/NaumRedlo/1984/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade"><img src="https://app.codacy.com/project/badge/Grade/f89a6f6b9bac40f09b6fa29a577d202c"/></a>

A Telegram bot for osu! players, and the replay engine that grew out of it.

> *"You are no longer a participant — you have become part of the system itself."*

---

## What this is

**The bot** tracks osu! accounts for a Telegram group: profile cards, recent
plays, leaderboards, a collection of titles, and weighted-pp top plays. It talks
to the osu! API v2, keeps its own database, and renders every card itself with
Pillow.

**Dossier** is an osu! replay engine written from scratch in Rust, and it lives
in [its own repository](https://github.com/NaumRedlo/Dossier). Replays are no
longer rendered through the bot: the Dossier application renders on the
player's own machine, and the bot is what it pairs with.

---

## The Dossier application

What the bot does for the application, and nothing more:

- **pairs it** — the application asks for a code, the person confirms it in a
  private chat with the bot (`/start pair-XXXX-XXXX`), and the machine gets a
  token of its own;
- **tells it who it belongs to** — the person, their avatar, their profile
  card, their osu! friends, and the groups they share with the bot;
- **shows it the group** — people, live plays, what happened this week, the
  week's moves and the titles;
- **delivers the finished video** to the person's private chat or to a group
  they are in.

All of it is under `/render/*` (`services/render_farm/http.py`) and exists only
while `RENDER_WORKER_TOKEN` is set. Pairing is open to the Telegram ids in
`RENDER_TESTER_IDS` (`*` for everybody).

Pairing is rate-limited per address, and the address is read from the
`X-Forwarded-For` entry the proxy in front of the bot added — the last one. If
there is more than one proxy in front of it (a CDN, then Caddy), set
`TRUSTED_PROXY_HOPS` to how many there are; the entries before those are
whatever the client chose to send.

### The engine, for pp

The engine binary is still what the bot counts pp with (`utils/osu/assay.py`,
falling back to rosu-pp-py). Which release it runs is the commented `dossier @`
line in `requirements.txt`; `scripts/engine.py` downloads that release, checks
it against the published hash and moves a symlink:

```bash
./venv/bin/python scripts/engine.py
```

`.env` names the link once: `DOSSIER_BIN=/root/.dossier/engine/dossier`. Run it
as `ExecStartPre=-…/scripts/engine.py` in the systemd unit so the bot and the
engine cannot drift; the leading `-` keeps a GitHub outage from stopping the
bot. Previous versions stay under `~/.dossier/engines` (`--list`, `--force`).

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

### Tests

```bash
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest -q
```

GitHub Actions runs the same on every push and pull request, on Python 3.11
and 3.12, and nothing in the suite reaches the network. Dependabot proposes
updates to the pinned packages and the actions once a week.

---

## Built with

| | |
|---|---|
| **Bot** | Python 3.12, aiogram 3.29, SQLAlchemy 2.0 (async) over SQLite, Pillow |
| **pp** | the Dossier engine binary, rosu-pp-py as the fallback |
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
