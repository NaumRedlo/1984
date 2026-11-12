# The server apart from the bot

A plan, written on 13 November 2026. Nothing here is built until a step says
so in its own commit.

## Why

Everything runs in one Python process started by `python -m bot.main`:

- the Telegram polling and the command handlers;
- the HTTP side of Dossier: 58 routes under `/render/`, installed into the
  OAuth server on `127.0.0.1:8080` (`services/oauth/server.py`);
- five loops: the profile updater, the live tracker, the roster sweep, the map
  checks, and the duels with their IRC connection to Bancho.

Three things follow from that.

1. One restart drops everything. `systemctl restart 1984` closes the app's
   sockets, the duels in progress and the Telegram polling together, and the
   unit waits 15 seconds before it starts again. The render queue
   (`render_farm/queue.py`) and the pairings waiting for an answer
   (`render_farm/pairing.py`) live in memory and are lost.
2. The HTTP side calls Telegram directly in 33 places of
   `render_farm/http.py`: faces, membership in a chat, sending a card or a
   video. Every shared video has a Telegram `file_id`
   (`shared_video.file_id` is not nullable) and reaches the app as a stream
   through the bot. When Telegram is slow or closed, the app is too.
3. A person is found by `telegram_id` first. The other way is half there:
   `RenderWorkerToken.player_id` and `OAuthToken.player_id` already name a
   player without Telegram.

What makes the split possible is already in place: the data is in PostgreSQL,
so several processes can share it, and Assay runs as a service of its own.

## What the machine has

From the output of 13 November 2026:

| | |
|---|---|
| Memory | 1.8 GiB, 289 MiB available, 198 MiB of swap in use |
| Disk | 30 GB, 6.4 GB free |
| In front | Caddy |
| Unit | `1984.service`, `User=root`, `Restart=always`, `RestartSec=15` |

Two limits come from this.

- **Memory.** Every further Python process loads SQLAlchemy, aiohttp and
  Pillow again. Three processes may not fit. The plan starts with two and
  measures before a third.
- **Disk.** One full allowance of 5 GB of videos takes most of the free space.
  The allowance cannot be sold before the videos have a place outside this
  disk.

One leftover to remove: the drop-in
`/etc/systemd/system/1984.service.d/engine.conf` runs `scripts/engine.py`
before every start. The script left the repository in commit `095eb5d6`; the
leading `-` hides the failure.

## Where it goes

```
Dossier ── HTTPS, WebSocket ──▶ server   the app's routes, sockets, files,
                                  │       duels and Bancho, the loops
                              PostgreSQL
                                  │
Telegram ◀──────────────────── bot       commands, notices, chat rosters
```

Two units from one repository: `1984-server` and `1984-bot`. The loops stay in
the server at first, because the duels speak to the app's sockets in memory and
Bancho allows one connection. A third unit for the loops is the last step and
only if the memory allows.

Rules the code keeps after the split:

- The database is the only thing the processes share. Nothing one process
  needs from the other lives in memory.
- The server does not import `aiogram`. The bot does not serve Dossier.
- The server asks Telegram for something by writing a row; the bot does it and
  writes the answer. `services/duels/outbox.py` already works this way for
  duel commands.
- The addresses older Dossier builds use keep answering.

## Seams found so far

Read from the code, not yet a full list; step 0 makes the list complete.

| Seam | Where | What it needs |
|---|---|---|
| Pairing | the app asks the server for a code, the person answers in the bot (`bot/handlers/start`), both read `pairing._pending` | a table with an expiry |
| Render queue | jobs are offered and taken in `RenderQueue._jobs` | a table |
| Tokens | `invites._owners` is a cache filled at start | read through, or told about a change |
| Faces and chat photos | `_bot.get_chat`, `get_file`, `download_file` | files the bot fills, the server reads |
| Membership | `members.is_member(_bot, …)` | the roster the sweep already keeps in the database |
| Sending a card or a video to a chat | `_bot.send_photo`, `send_video`, `send_document` | an outbox row |
| Videos read back from Telegram | `_fetched(_bot, video.file_id, …)` | the store below |
| Live tracker nudge | `live_tracker.nudge` from the routes | stays in memory while the loops live in the server |
| Duel notices | `tasks/duels.py` sends through the bot | an outbox row |

## Storage

Decided by the owner on 13 November 2026:

- Everyone, subscriber or not: the replay is kept on the server and can be
  downloaded. The video is not kept.
- Tiers above *Сторонник* (*Делегат*, *Комиссар*, *Советник*, *Член
  Президиума*): up to 5 GB of videos kept on the server.

What is there today: replays of a player are kept already
(`render_farm/replays.py`, 300 each, 4 GB together); a video shared in the app
is kept for 72 hours under `APP_VIDEOS_DIR` with 4 GB a person and 20 GB
together, three times what the disk has free.

What changes:

- The allowance comes from the subscription: `subscriptions.has_access` and
  the tier. This is the first thing the subscription gates.
- `shared_video.file_id` becomes nullable: a video may live in the store with
  no copy in Telegram, or in Telegram with no copy in the store.
- A `Store` with two kinds behind it: a folder on this disk, and a bucket that
  speaks the S3 protocol. Routes do not know which one answers.

Open, the owner's to decide:

- where the bytes live once the disk is not enough (a larger disk at the same
  host, a storage box, or a bucket) and whether players in Russia reach it
  directly or through this server;
- what happens to kept videos when the subscription ends;
- what happens to the 72-hour sharing for people without the allowance: the
  decision above reads as "the replay travels, the receiver renders it".

## The address

Dossier has one address compiled in, `DEFAULT_SERVER` in
`native/src/settings.rs`: `onenineeightfour.ignorelist.com`, a name under a
free dynamic DNS domain that belongs to somebody else.

A name of our own is the owner's decision and is not taken yet. Whatever it
is, the app should stop depending on one name:

- the server says its preferred address in the first answer to a signed-in
  app, and the app saves it;
- the app carries a spare address and tries it when the saved one does not
  answer;
- the old name keeps pointing at the machine for as long as old builds are in
  use.

With that, moving to another name or another machine needs no release.

## Steps

Each step is released alone and leaves the testers' builds working. The sizes
are estimates.

| # | Step | Done when | Size |
|---|---|---|---|
| 0 | List every place where the app's side reaches the bot and the bot reaches the app's memory. A test holds the list: a new import across the line fails it. | the test is in CI | small |
| 1 | A narrow port for Telegram in the routes: membership, a face, send to a chat, read a video. The first kind behind it calls the bot as today. | `render_farm/http.py` has no `_bot`; behaviour and tests unchanged | medium |
| 2 | The outbox of the duels made general; a second kind of the port that writes rows the bot carries out. | a card sent from the app arrives in the chat with the routes and the bot in different processes on a development machine | medium |
| 3 | Pairings and the render queue in tables; tokens read without a cache that goes stale. | a restart in the middle of a pairing and of a queued render loses neither | medium |
| 4 | Two entry points and two units, a user that is not root, a health address, Caddy told to wait for it. Clients are told before a stop. | `systemctl restart 1984-bot` does not close an app's socket, and a server restart does not stop the polling | medium |
| 5 | What the bot changes and the app shows crosses over with `LISTEN`/`NOTIFY`. | a command in a chat shows in a signed-in app without a poll | small |
| 6 | The store and the allowance by tier. | a subscriber above *Сторонник* keeps a video past 72 hours; a person without the tier gets the replay | large |
| 7 | Signing in without Telegram from end to end, and `/v1/` beside `/render/`. | a new person signs in with osu! alone and uses everything that does not need a chat | medium |
| 8 | Keeping it up: a migration tool, since `create_all` adds no columns; a nightly dump copied off the machine; a monitor from outside. | a restore of last night's dump is rehearsed once | medium |
| 9 | The loops as a third unit. | only if step 4 leaves the memory for it | medium |

Steps 0 to 4 are the split. After them the bot is no longer the only way in.

## Before step 4

Needed from the machine, not known yet:

```
ps -eo rss,comm,args --sort=-rss | head -12
du -sh /root/1984/data/* /root/1984/logs /var/lib/postgresql 2>/dev/null
cat /etc/caddy/Caddyfile
```

The first says how much each process takes and whether a local Telegram Bot
API server runs beside the bot. The second says what fills the 22 GB. The
third is the place the second unit and the health check are added.
