import types
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.migrations.add_app_accounts import run_app_accounts_migration
from db.models.best_score import UserBestScore
from db.models.oauth_token import OAuthToken
from db.models.player import Player
from db.models.render_worker_token import RenderWorkerToken
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from services.oauth import server as oauth
from services.refresh import refresh_user
from services.render_farm import http, invites, pairing, players
from tasks.live_tracker import LiveTracker
from tasks.profile_updater import ProfileUpdater

MAC = pairing.Machine("MacBook Pro", "macOS", 10, "0.93.0")
OSU = {
    "id": 1001, "username": "alice", "country": {"code": "RU"}, "avatar_url": "https://a.ppy.sh/1001",
    "cover": {"url": "https://assets.ppy.sh/cover.jpg"},
    "statistics": {"pp": 4321.7, "global_rank": 50_000, "hit_accuracy": 98.123, "play_count": 12_000, "play_time": 360_000,
                   "ranked_score": 9_000_000, "total_hits": 2_400_000, "total_score": 20_000_000},
}

@pytest.fixture(autouse=True)
def clean_pairing():
    pairing._pending.clear()
    pairing._osu_states.clear()
    pairing._starts.clear()
    yield
    pairing._pending.clear()
    pairing._osu_states.clear()
    pairing._starts.clear()

@pytest_asyncio.fixture
async def factory(monkeypatch):
    import db.database as database

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "AsyncSessionFactory", made)
    monkeypatch.setattr(oauth, "get_db_session", made)
    yield made
    await engine.dispose()

def test_a_pairing_can_be_answered_through_osu_and_only_once():
    code = pairing.start(MAC, "1.1.1.1")
    state = pairing.osu_state(code)
    assert state.startswith("app.") and pairing.osu_state("22222222") is None
    assert pairing.osu_state(code) == state and len(pairing._osu_states) == 1
    assert pairing.take_osu_state(state) == code
    assert pairing.take_osu_state(state) is None
    linked = pairing.approve(code, 0, "alice", player_id=5)
    assert (linked.linked_to, linked.linked_player) == (0, 5)
    assert pairing.osu_state(code) is None

async def test_a_request_to_link_telegram_is_not_a_machine_waiting_for_a_token():
    code = pairing.ask_telegram(5, "alice", "1.1.1.1")
    assert pairing.describe(code).wants_telegram_for == 5
    assert pairing.approve(code, 7, "Naum") is None
    assert (await pairing.collect(code, "1.1.1.1")).status == pairing.GONE
    assert pairing.answered(code).wants_telegram_for == 5
    assert pairing.answered(code) is None

async def test_signing_in_makes_a_player_without_telegram_and_fills_it_from_osu(factory):
    async with factory() as s:
        player = await players.signed_in(s, OSU)
        await s.commit()
        assert (player.osu_user_id, player.telegram_id, player.osu_username, player.country) == (1001, None, "alice", "RU")
        assert (player.player_pp, player.accuracy, player.play_count, player.cover_url) == (4321, 98.12, 12_000, "https://assets.ppy.sh/cover.jpg")
        again = await players.signed_in(s, {**OSU, "username": "alice2"})
        assert again.id == player.id and again.osu_username == "alice2"
        assert await players.signed_in(s, {"username": "nobody"}) is None

async def test_signing_in_as_someone_the_bot_knows_leaves_their_chat_data_alone(factory):
    async with factory() as s:
        s.add(User(chat_id=-100, telegram_id=7, osu_username="alice", osu_user_id=1001, player_pp=5000))
        await s.commit()
        player = await players.signed_in(s, OSU)
        await s.commit()
        assert (player.telegram_id, player.player_pp) == (7, 5000)

async def _callback(monkeypatch, state, *, osu=OSU, headers=None):
    async def exchanged(_code):
        return {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}

    async def me(_token):
        return osu

    refreshed = []

    async def first(player_id):
        refreshed.append(player_id)

    monkeypatch.setattr(oauth, "encrypt_token", lambda secret: secret.encode())
    monkeypatch.setattr(oauth, "_exchange_code", exchanged)
    monkeypatch.setattr(oauth, "_get_oauth_user", me)
    monkeypatch.setattr(oauth, "_first_refresh", first)
    monkeypatch.setattr(oauth, "spawn", lambda coro, name=None: coro.close())
    request = types.SimpleNamespace(query={"code": "from-osu", "state": state}, headers=headers or {})
    return await oauth.handle_callback(request)

async def test_osu_calls_back_and_the_waiting_application_gets_a_token_of_its_player(factory, monkeypatch):
    code = pairing.start(MAC, "1.1.1.1")
    state = pairing.osu_state(code)
    reply = await _callback(monkeypatch, state, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
    assert reply.status == 200 and "alice" in reply.text and "Вход выполнен" in reply.text
    got = await pairing.collect(code, "1.1.1.1")
    assert got.status == pairing.LINKED and got.who == "alice"
    owner = invites.owner(got.token)
    async with factory() as s:
        player = (await s.execute(select(Player))).scalar_one()
        token = (await s.execute(select(OAuthToken))).scalar_one()
        kept = (await s.execute(select(RenderWorkerToken))).scalar_one()
    assert owner == invites.Owner(0, "alice", player.id)
    assert (token.telegram_id, token.player_id, token.scopes) == (None, player.id, oauth.OSU_OAUTH_SCOPES)
    assert (kept.issued_to, kept.player_id) == (None, player.id)
    assert player.id in invites.linked_players() and 0 not in invites.linked()
    invites.forget(invites.digest(got.token))

async def test_a_state_that_is_used_or_unknown_is_refused(factory, monkeypatch):
    code = pairing.start(MAC, "1.1.1.1")
    state = pairing.osu_state(code)
    assert (await _callback(monkeypatch, state)).status == 200
    late = await _callback(monkeypatch, state)
    assert late.status == 400 and "expired" in late.text
    assert (await _callback(monkeypatch, "app.unknown")).status == 400

async def test_enrolled_machines_come_back_with_their_players_after_a_restart(factory):
    async with factory() as s:
        s.add(Player(id=3, osu_user_id=3, osu_username="alice"))
        await s.flush()
        s.add_all([
            RenderWorkerToken(digest="a" * 64, issued_to=7, issued_name="Naum"),
            RenderWorkerToken(digest="b" * 64, issued_to=None, player_id=3, issued_name="alice"),
            RenderWorkerToken(digest="c" * 64, issued_to=None, player_id=None),
        ])
        await s.commit()
    assert await invites.load() == 3
    assert invites._owners["a" * 64] == invites.Owner(7, "Naum", None)
    assert invites._owners["b" * 64] == invites.Owner(0, "alice", 3)
    assert "c" * 64 not in invites._owners
    assert invites.linked() == {7} and invites.linked_players() == {3}
    for digest in ("a" * 64, "b" * 64, "c" * 64):
        invites.forget(digest)

class _Bot:
    def __init__(self):
        self.asked = []

    async def get_chat(self, chat_id):
        self.asked.append(chat_id)
        return types.SimpleNamespace(username="naumredlo", first_name="Naum", last_name="", photo=None, title="", full_name="Naum")

    async def get_chat_member(self, chat_id, telegram_id):
        return types.SimpleNamespace(status="left")

@pytest_asyncio.fixture
async def alone(factory, monkeypatch):
    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "a-shared-secret")
    from config import settings

    monkeypatch.setattr(settings, "OSU_CLIENT_ID", "11")
    monkeypatch.setattr(settings, "OSU_CLIENT_SECRET", "secret")
    async with factory() as s:
        player = await players.signed_in(s, OSU)
        s.add(Player(osu_user_id=1002, telegram_id=None, osu_username="orphan"))
        await s.flush()
        s.add_all([
            UserBestScore(player_id=player.id, score_id=1, beatmap_id=1, pp=412.6, title="FREEDOM DiVE", artist="xi", version="Extra"),
            UserTitleProgress(player_id=player.id, title_code="wysi", current_value=1, unlocked=True, unlocked_at=datetime(2026, 9, 1)),
            User(chat_id=-100, telegram_id=8, osu_username="bob", osu_user_id=1003, player_pp=3000),
        ])
        await s.commit()
        player_id = player.id
    token = "a-personal-token-of-sixty-four-characters-more-or-less-long-osu"
    invites.remember(token, invites.Owner(0, "alice", player_id))
    bot = _Bot()
    http.set_bot(bot)
    http.set_osu(None)
    app = web.Application()
    app.add_routes(http.make_routes())
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}, player_id, bot
    await client.close()
    http.set_bot(None)
    invites.forget(invites.digest(token))

async def test_an_account_without_telegram_says_so_and_has_no_chats(alone):
    client, mine, player_id, bot = alone
    me = await (await client.get("/render/me", headers=mine)).json()
    assert me == {"telegram_id": 0, "name": "alice", "username": "", "avatar": False, "telegram": False, "player": player_id}
    assert await (await client.get("/render/me/chats", headers=mine)).json() == []
    assert (await client.get("/render/me/avatar", headers=mine)).status == 404
    assert bot.asked == []
    assert (await client.post("/render/send", headers=mine, data=b"mp4")).status == 409

async def test_it_has_its_profile_and_stands_among_the_players(alone):
    client, mine, player_id, _ = alone
    body = await (await client.get("/render/community", headers=mine)).json()
    assert body["chat"] is None and body["people"] == []
    me = body["me"]
    assert (me["name"], me["pp"], me["you"], me["player"], me["titles"]) == ("alice", 4321, True, player_id, ["wysi"])
    assert me["top"][0]["pp"] == 412.6 and me["id"] == 0
    listed = await (await client.get("/render/players", headers=mine)).json()
    assert [(card["name"], card["you"]) for card in listed["people"]] == [("alice", True), ("bob", False)]
    opened = await (await client.get(f"/render/players/{player_id}", headers=mine)).json()
    assert opened["you"] is True and opened["title_progress"] == {"wysi": 1}

async def test_it_wears_a_title_shares_a_card_and_has_an_inbox(alone, factory):
    client, mine, player_id, _ = alone
    assert (await client.post("/render/me/title", headers=mine, json={"code": "wysi"})).status == 200
    assert (await client.post("/render/me/title", headers=mine, json={"code": "magic7"})).status == 403
    assert (await (await client.post("/render/me/profile", headers=mine, json={"name": "alice"})).json()) == {"kept": 1}
    box = await (await client.get("/render/me/inbox", headers=mine)).json()
    assert box["registered"] is True and box["videos"] == []
    assert (await (await client.post("/render/me/accept", headers=mine, json={"from": "everyone"})).json()) == {"accept": "everyone"}
    assert (await (await client.get("/render/me/replays", headers=mine)).json())["name"] == "alice"
    assert (await (await client.get("/render/me/pin", headers=mine)).json())["chat"] is None
    async with factory() as s:
        player = await s.get(Player, player_id)
        assert (player.active_title_code, player.accept_videos) == ("wysi", "everyone") and "alice" in player.app_profile

async def test_the_application_is_sent_to_osu_and_can_ask_for_telegram_later(alone):
    client, mine, player_id, _ = alone
    started = await (await client.post("/render/pair", json={"name": "mac"})).json()
    assert started["osu"] is True
    code = invites.tidy(started["code"])
    away = await client.get(f"/render/pair/{code}/osu", allow_redirects=False)
    assert away.status == 302 and away.headers["Location"].startswith("https://osu.ppy.sh/oauth/authorize?client_id=")
    assert "state=app." in away.headers["Location"]
    assert (await client.get("/render/pair/22222222/osu", allow_redirects=False)).status == 404
    asked = await (await client.post("/render/me/telegram", headers=mine)).json()
    assert pairing.describe(asked["code"]).wants_telegram_for == player_id

async def test_linking_telegram_gives_the_account_its_chats_and_moves_its_osu_token(factory):
    async with factory() as s:
        player = await players.signed_in(s, OSU)
        other = await players.signed_in(s, {**OSU, "id": 1002, "username": "taken"})
        other.telegram_id = 9
        s.add(OAuthToken(player_id=player.id, access_token_enc=b"x"))
        await s.commit()
        assert await players.link_telegram(s, player.id, 9) == players.TAKEN
        assert await players.link_telegram(s, 999, 7) == players.GONE
        assert await players.link_telegram(s, player.id, 7) == players.LINKED
        assert await players.link_telegram(s, player.id, 7) == players.LINKED
        assert await players.link_telegram(s, player.id, 8) == players.TAKEN
        token = (await s.execute(select(OAuthToken))).scalar_one()
        assert (token.telegram_id, token.player_id) == (7, player.id)
        assert (await s.get(Player, player.id)).telegram_id == 7

async def test_the_bot_asks_before_linking_and_links_on_yes(factory, monkeypatch):
    from bot.handlers.start import handlers
    from tests.unit.test_farm_pairing import _Callback, _Message, _command

    async def english(_user_id):
        return "en"

    async def strangers(_bot, _telegram_id):
        return False

    monkeypatch.setattr(handlers, "get_language", english)
    monkeypatch.setattr(handlers.members, "shares_a_group", strangers)
    monkeypatch.setattr(handlers, "can_use_render", lambda _telegram_id: False)
    import db.database as database

    monkeypatch.setattr(database, "get_db_session", factory)
    async with factory() as s:
        player = await players.signed_in(s, OSU)
        await s.commit()
    code = pairing.ask_telegram(player.id, "alice", "1.1.1.1")
    message = _Message(user_id=7)
    await handlers.start_pairing(message, _command(f"pair-{code}"))
    text_shown, kw = message.sent[-1]
    assert "alice" in text_shown and "Link this Telegram" in text_shown
    assert kw["reply_markup"].inline_keyboard[0][0].text == "Yes, link"
    callback = _Callback(f"pair:yes:{code}", user_id=7)
    await handlers.answer_pairing(callback)
    assert "linked to this Telegram" in callback.message.sent[-1][0]
    async with factory() as s:
        assert (await s.get(Player, player.id)).telegram_id == 7
    again = _Callback(f"pair:yes:{code}", user_id=7)
    await handlers.answer_pairing(again)
    assert again.message.sent == [] and again.answered[0][1].get("show_alert") is True

class _Api:
    def __init__(self):
        self.asked = []

    async def sync_user_stats_from_api(self, user, oauth_token=None):
        self.asked.append(("stats", type(user).__name__))
        user.player_pp = 4400
        user.last_api_update = datetime(2026, 10, 1)
        return True

    async def sync_user_best_scores(self, user, session, oauth_token=None):
        self.asked.append(("best", user.player_id))
        return True

async def test_a_player_without_a_chat_row_is_refreshed_and_gets_titles(factory, monkeypatch):
    async def no_token(_user):
        return None

    import services.oauth.token_manager as tokens

    monkeypatch.setattr(tokens, "token_of", no_token)
    async with factory() as s:
        player = await players.signed_in(s, OSU)
        await s.commit()
        api = _Api()
        assert await refresh_user(player, s, api, mode="full")
        await s.commit()
        assert api.asked == [("stats", "Player"), ("best", player.id)]
        assert player.player_pp == 4400 and player.last_full_update is not None
        unlocked = (await s.execute(select(UserTitleProgress.title_code).where(
            UserTitleProgress.player_id == player.id, UserTitleProgress.unlocked.is_(True)))).scalars().all()
        assert "registered" in unlocked

async def test_the_updater_and_the_tracker_look_after_signed_in_players_without_rows(factory, monkeypatch):
    async with factory() as s:
        alice = await players.signed_in(s, OSU)
        fresh = await players.signed_in(s, {**OSU, "id": 1002, "username": "fresh"})
        fresh.last_full_update = datetime.now(timezone.utc).replace(tzinfo=None)
        await players.signed_in(s, {**OSU, "id": 1003, "username": "unsigned"})
        s.add(User(chat_id=-100, telegram_id=8, osu_username="bob", osu_user_id=1004))
        await s.commit()
        bob = (await s.execute(select(User))).scalar_one()
        monkeypatch.setattr(invites, "linked_players", lambda: {alice.id, fresh.id, bob.player_id})
    import tasks.profile_updater as updating

    monkeypatch.setattr(updating, "AsyncSessionFactory", factory)
    updater = ProfileUpdater(api_client=None)
    assert await updater.get_stale_player_ids() == [alice.id]
    tracker = LiveTracker(api_client=None)
    assert sorted(await tracker.known()) == [1001, 1002, 1004]

async def test_joining_a_group_later_links_the_same_player_and_keeps_what_it_had(factory):
    async with factory() as s:
        player = await players.signed_in(s, OSU)
        player.active_title_code = "wysi"
        player.share_replays = True
        player.active_streak_best = 12
        await s.commit()
        s.add(User(chat_id=-100, telegram_id=7, osu_username="alice", osu_user_id=1001, player_pp=4500))
        await s.commit()
        row = (await s.execute(select(User))).scalar_one()
        await s.refresh(player)
        assert row.player_id == player.id and player.telegram_id == 7
        assert (row.active_title_code, row.share_replays, row.active_streak_best) == ("wysi", True, 12)
        assert (player.active_title_code, player.share_replays, player.player_pp) == ("wysi", True, 4500)
        assert await s.scalar(select(func.count()).select_from(Player)) == 1

@pytest.mark.sqlite_only
async def test_older_tables_gain_what_app_accounts_need():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE oauth_tokens"))
            await conn.execute(text(
                "CREATE TABLE oauth_tokens (id INTEGER PRIMARY KEY, telegram_id BIGINT NOT NULL UNIQUE, access_token_enc BLOB NOT NULL,"
                " refresh_token_enc BLOB, token_expiry DATETIME, scopes VARCHAR(255), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
            await conn.execute(text("INSERT INTO oauth_tokens VALUES (1, 7, x'01', NULL, NULL, 'public', '2026-01-01', '2026-01-01')"))
            await conn.execute(text("DROP TABLE render_worker_tokens"))
            await conn.execute(text(
                "CREATE TABLE render_worker_tokens (id INTEGER PRIMARY KEY, digest VARCHAR(64) NOT NULL UNIQUE, issued_to BIGINT,"
                " issued_name VARCHAR(128), worker VARCHAR(128), created_at DATETIME NOT NULL, last_seen DATETIME, revoked_at DATETIME)"))
            await conn.execute(text("ALTER TABLE players DROP COLUMN last_full_update"))
        await run_app_accounts_migration(engine)
        await run_app_accounts_migration(engine)
        async with engine.begin() as conn:
            assert (await conn.execute(text("SELECT telegram_id, player_id, scopes FROM oauth_tokens"))).all() == [(7, None, "public")]
            await conn.execute(text("INSERT INTO oauth_tokens (player_id, access_token_enc, created_at, updated_at) VALUES (3, x'02', '2026-01-01', '2026-01-01')"))
            assert "player_id" in {row[1] for row in (await conn.execute(text("PRAGMA table_info(render_worker_tokens)"))).all()}
            assert "last_full_update" in {row[1] for row in (await conn.execute(text("PRAGMA table_info(players)"))).all()}
    finally:
        await engine.dispose()
