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
in [its own repository](https://github.com/NaumRedlo/Dossier). The bot draws no
replay itself: the Dossier application renders on the player's own machine,
and machines lent to the farm render the replays people send the bot.

---

## The Dossier application

What the bot does for the application, and nothing more:

- **pairs it** — the application asks for a code, the person confirms it in a
  private chat with the bot (`/start pair-XXXX-XXXX`), and the machine gets a
  token of its own;
- **signs it in without Telegram** — the same code can be answered through
  osu! instead (`/render/pair/{code}/osu` leads to osu!'s consent page and back
  to `/oauth/callback`): the bot finds or makes the player of that osu!
  account, keeps their osu! token, and the machine's token belongs to the
  player. Such a player has a profile, stands among the players, has an inbox
  and a journal, is refreshed like everyone else, and has no chats: sending a
  video asks for Telegram first. Telegram is linked later from the application
  (`POST /render/me/telegram` gives a code, the bot asks in a private chat and
  links on yes) or simply by registering the same osu! account in a group;
- **tells it who it belongs to** — the person, their avatar, their profile
  card, their osu! friends, and the groups they share with the bot;
- **shows it the group** — people, live plays, what happened this week, the
  week's moves and the titles;
- **delivers the finished video** to the person's private chat or to a group
  they are in;
- **hands it work** when its person switches the worker on: the farm.

All of it is under `/render/*` (`services/render_farm/http.py`) and exists only
while `RENDER_WORKER_TOKEN` is set. Pairing is open to the Telegram ids in
`RENDER_TESTER_IDS` (`*` for everybody).

### The farm

Anyone who shares a group with the bot can send it an osu!standard `.osr` — in
a private chat or in a group, the same — and gets the video back as a reply.
The bot reads the replay's header, finds the map by its hash, and queues a job
(`services/render_farm/queue.py`); the reply that says so is kept current —
the place in the queue and how many workers are online, then who took it and
how far along it is. A paired application with its worker on claims the oldest
job (`POST /render/claim`), fetches the replay and the chosen skin
(`/render/job/<id>/replay`, `/skin`), finds or downloads the map itself,
reports progress (`/heartbeat`) and uploads the video (`/result`) or hands the
job back (`/give-back`); `/render/farm` lists who is online. A job is leased for
90 seconds at a time and goes back in line when its worker goes quiet; after
three tries, or when nobody takes it within `RENDER_GIVE_UP` seconds (900), the
person is told. Every video is 1920×1080 at 60 FPS with normalised loudness; the
skin is the person's choice in `sts` → *Render skin*, from the `.osk` files in
`RENDER_SKINS_DIR` (`data/skins`), or Dossier Default. A registered group member
can upload an `.osk` or ZIP in the bot chat; a validated upload is selected
automatically and appears only in that person's skin menu. Uploaded archives
are immutable and addressed by content hash under `data/skins/uploads/<telegram-id>`;
queued jobs retain their original archive when the person uploads a new skin.
`RENDER_SKIN_MOST` defaults to 64 MiB per archive (maximum 256 MiB),
`RENDER_SKIN_STORAGE_MOST` to 512 MiB per person. Archives expand to at most
512 MiB and 8192 entries and must contain a single skin; invalid archives
do not replace the selected skin. If storage is full, an administrator can
remove unused uploads after the render queue is empty. A person may have
`RENDER_ORDERS_EACH` (2) jobs open at once, and a replay may be
`RENDER_REPLAY_MOST` bytes (8 MB).

A paired Dossier app whose owner has opted in may donate the replays it finds
on the device to the engine's corpus through `POST /render/me/replay`. Each
replay is checked for an osu! replay header, kept once under its MD5 in
`DONATED_REPLAYS_DIR` (`data/donated-replays`) with a line in `index.tsv`
naming who sent it, and refused once the folder holds
`DONATED_REPLAYS_STORAGE_MOST` bytes (2 GiB).

A video sent through `POST /render/send` is remembered by its Telegram
`file_id`; the server keeps no video files. Its owner can pass it on to other
players (`POST /render/videos/{id}/share`) who have Dossier paired and take
videos: from people of a shared chat (the default), from everyone, or from
nobody (`POST /render/me/accept`). `GET /render/videos/receivers` lists who would
take one. The receiver's inbox (`GET /render/me/inbox`) names the sender, the
play, the length and the size; `/render/inbox/{id}/video` streams the file from
Telegram and removes the copy the local Bot API server made as soon as it has
been passed on, `/thumb` serves Telegram's thumbnail, `/telegram` forwards the
video to the receiver's chat with the bot, `/replay` returns the replay the
sender attached (`PUT /render/videos/{id}/replay`, kept once under its MD5 in
`SHARED_REPLAYS_DIR`, `data/shared-replays`, up to
`SHARED_REPLAYS_STORAGE_MOST` bytes, 1 GiB) so the receiver's Dossier can draw
the same video itself. One person may send 60 videos a day, 20 receivers at a
time, and an inbox holds 100.

A player who switches *share my replays* on in Dossier (`POST /render/me/replays`)
gives the bot their own osu! standard plays through `POST /render/replays`: the
replay must carry the player's osu! name, is kept once under its MD5 in
`PLAYER_REPLAYS_DIR` (`data/player-replays`, `PLAYER_REPLAYS_STORAGE_MOST`
bytes, 4 GiB) with the map's name beside it — the app's words, or osu!'s when
the app had none — and only the newest `PLAYER_REPLAYS_EACH` (300) per player
stay. `GET /render/replays` lists the 200 newest plays of the people who share
a chat with the asker (`scope=all`: of every sharing player), and
`GET /render/replays/{md5}` hands one over. Switching sharing off removes the
player's replays.

Pairing is rate-limited per address, and the address is read from the
`X-Forwarded-For` entry the proxy in front of the bot added — the last one. If
there is more than one proxy in front of it (a CDN, then Caddy), set
`TRUSTED_PROXY_HOPS` to how many there are; the entries before those are
whatever the client chose to send.

### pp and star ratings

pp, star ratings with mods and the difficulty graphs on the cards all come from
[assay-server](assay-server/README.md), which runs osu!'s own packages beside the
bot (`ASSAY_URL` in `.env`): a recent play, what-if, star ratings with mods
(their settings included, so DT ×1.2 is ×1.2), the strain graph, and plays osu!
gave no pp — loved, unranked — whose estimate the map leaderboard shows with a
`~`. A ranked play whose pp differs from osu!'s by more than 1% is logged as
`pp drift`. Keeping it current is a Dependabot pull request for the
`ppy.osu.Game*` packages.

There is no second source: without the service a card shows osu!'s own pp and
star rating and leaves out what only the service can tell (if FC, if SS, the
graph, what-if). The bot says at start-up whether the service answers.

---

## The bot

| Feature | |
|---|---|
| 👤 **Profiles** | Auto-refreshing profile cards, recent plays, head-to-head comparison |
| 📊 **Leaderboards** | Six categories: pp, accuracy, play count, play time, ranked score, hits per play |
| 🏅 **Titles** | Achievements across seven rarities, with progress bars and an active title on the profile card |
| 📈 **Top plays** | Best scores by weighted pp — the same `0.95^(N-1)` curve osu! itself uses — with change tracking |

### Who is in a chat

A person's data is kept in two places: what is theirs anywhere (the osu!
account, its statistics, scores, titles) on the player, and what belongs to one
chat (points, duels, the weekly board) on that chat's row. The bot keeps each
chat's rows in step with who is really there:

- every `MEMBERSHIP_EVERY_HOURS` (6; `0` switches it off) it asks Telegram
  about every row of every group, and about every known player who has no row
  in a group; a service message about someone leaving or joining is acted on
  at once;
- a known player found in a group gets a row there without registering again;
- whoever has left or was removed loses the row, the weekly snapshots of that
  chat and the pin on it. What is shared stays with the player, and what
  belonged to the chat is put aside in `left_members` for a year and given back
  if they return;
- only a plain "left" or "kicked" from Telegram counts. An error, an unknown
  status, a group the bot cannot confirm it is in, or a group where every
  player at once seems gone removes nobody.

The admin command `members` shows what a sweep would change without changing
anything; `members sync` applies it at once.

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

### lava.top integration

`services/lava_top.py` provides an opt-in API adapter for subscription checkout,
invoice and subscription lookup, cancellation, authenticated webhook parsing,
and complete paginated reads of products and subscriptions.
It follows the [lava.top API](https://developers.lava.top/en) and
[OpenAPI schema](https://gate.lava.top/docs/documentation.yaml).
The authenticated `GET /render/billing/plans` endpoint returns selected tariffs
from lava.top, cached for 60 seconds. The same module registers the rest of the
billing routes, all keyed to the signed-in Dossier account's `Player`:
`GET /render/billing/status` (the account's subscription, checked against
lava.top at most every few seconds while a payment is pending),
`POST /render/billing/checkout` (`{"plan", "email"}`, the plan is resolved from the
catalogue on the server and a payment link comes back), `POST /render/billing/cancel`,
and `POST /render/billing/webhook` (authenticated with `LAVA_TOP_WEBHOOK_KEY`, not
with a Dossier token). State lives in `billing_subscriptions` and `billing_events`
(`services/render_farm/subscriptions.py`). Nothing in the bot is gated by it yet:
the status only reports `state` and `access`.

A held subscription refuses a second checkout with 409 `exists` unless the body
carries `"change": true`. A change is allowed to a higher tier, or to another period
or currency of the same tier; the plan already held answers `same`, a lower tier
answers `lower` (cancel and subscribe again after the paid period), and a tier that
cannot be compared in the catalogue answers `unknown`. Tiers are compared by the
price of a day at the same currency and period. While the new payment is pending the
status keeps showing the held subscription with the pending one under `change`.
Once it is paid, the server cancels the old subscription at lava.top, marks it
`replaced`, and carries its unused time over: the seconds left, multiplied by the old
price of a day over the new one, are stored in `bonus_seconds` and reported as
`carried_seconds`. Carried time is a tail after the paid period, so it counts once
the new subscription stops renewing; `paid_until` is the next payment while it
renews and the end of access afterwards. A cancel that lava.top does not take is
tried again on every later status check and webhook. The columns for this are added
by `db/migrations/add_billing_change.py` on SQLite and PostgreSQL alike.

`LavaConfig.from_env()` reads `LAVA_TOP_ENABLED` (false by default),
`LAVA_TOP_API_KEY` (the outgoing key from lava.top), and
`LAVA_TOP_WEBHOOK_KEY` (a separate secret generated on our server, at most
80 ASCII characters). Configure the latter as the webhook's `X-Api-Key`.
The webhook key may be omitted while only reading the catalogue; incoming
webhooks are rejected until it is configured.
Both secrets belong on the server, never in Dossier or a browser bundle.
`LAVA_TOP_PRODUCT_IDS` selects the subscription product UUIDs that belong to
Dossier, separated by commas. No selection means the catalogue stays unavailable.
Names, prices, currencies, and billing periods are read from lava.top, including
hidden products and periods longer than a month. A failed refresh returns 503
rather than serving old prices. Subscription lists are never exposed to clients.

To check the account before enabling billing, run `python -m scripts.lava_check`
from the server repository. It reads `LAVA_TOP_API_KEY` from the server environment
or `.env`, or prompts for it without echoing. It performs GET requests only and
prints the available tariffs with product IDs and subscription counts, not buyer
emails or credentials. Compare the count with the creator dashboard: the API
describes the subscription listing as scoped to the supplied API key, so do not
assume that historical purchases through other channels are included.

The points below are what the routes above follow. Checkout carries our own
reference in `clientUtm.utm_content`, so a webhook for an invoice whose creation
answer was lost can still be matched to its buyer.

- Resolve the selected catalogue price to a `SubscriptionOffer` on the server
  when adding checkout. Do not accept arbitrary offer IDs or amounts from clients.
- Resolve the signed-in Dossier account to `Player.id`, and persist ownership
  of each checkout before granting access. Buyer email is not account identity.
- Persist events using `WebhookEvent.deduplication_key` under a unique constraint;
  reconcile provider state before applying access changes. Delivery may be out of
  order. Cancellation keeps the paid-through date; it is not an immediate refund.
- Refund and chargeback payloads do not provide an invoice ID in the documented
  examples. Reconcile these explicitly; do not revoke access by email alone.
- Acknowledge authenticated unknown events with 2xx. Store known events durably
  before acknowledging them. Returning to a success URL never grants access.
- Do not blindly retry checkout creation after a timeout: the provider may have
  created the invoice. The adapter intentionally does not retry mutations.

The adapter tests use a local fake provider and make no real payment requests.

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
| **Bot** | Python 3.12, aiogram 3.29, SQLAlchemy 2.0 (async) over SQLite or PostgreSQL, Pillow |
| **pp** | [assay-server](assay-server/README.md): osu!'s own `ppy.osu.Game*` packages in .NET |
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
| `assets/fonts/Nunito-*` | Nunito (SIL OFL 1.1, `OFL-Nunito.txt`); static Regular, Bold and ExtraBold from Google Fonts (ExtraBold for bold text, Bold for semibold: drawn smoothed they read as Commissioner's Bold and SemiBold did) |
| `assets/fonts/MPLUSRounded1c-*` | M PLUS Rounded 1c (SIL OFL 1.1, `OFL-MPLUSRounded1c.txt`) |
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
