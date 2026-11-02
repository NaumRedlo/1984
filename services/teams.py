from typing import Optional

from sqlalchemy import select

from db.models.user import User


def _member(raw: dict, leader: bool) -> dict:
    country = raw.get("country") or {}
    return {
        "osu_user_id": raw.get("id"),
        "username": raw.get("username"),
        "avatar_url": raw.get("avatar_url"),
        "country": (country.get("code") or raw.get("country_code") or "").lower(),
        "country_name": country.get("name"),
        "is_leader": leader,
    }


async def team_card_data(session, team: dict, chat_id: Optional[int], lang: str, statistics: Optional[dict] = None) -> dict:
    leader = team.get("leader") or {}
    members = ([_member(leader, True)] if leader.get("id") else []) + [_member(m, False) for m in team.get("members") or [] if m.get("id")]
    ids = [m["osu_user_id"] for m in members]
    here = {}
    if chat_id is not None and ids:
        rows = (await session.execute(
            select(User.osu_user_id, User.player_pp, User.global_rank)
            .where(User.chat_id == chat_id, User.osu_user_id.in_(ids)))).all()
        here = {osu_id: (pp, rank) for osu_id, pp, rank in rows}
    for member in members:
        known = (statistics or {}).get(member["osu_user_id"])
        if known:
            member.update(pp=round(known.get("pp") or 0), global_rank=known.get("global_rank"),
                          accuracy=known.get("accuracy"), play_count=known.get("play_count"))
        if member["osu_user_id"] in here:
            pp, rank = here[member["osu_user_id"]]
            member["in_chat"] = True
            if not member.get("pp"):
                member.update(pp=pp or 0, global_rank=rank or None)
    leading = members[:1] if members and members[0]["is_leader"] else []
    rest = members[len(leading):]
    rest.sort(key=lambda m: (not m.get("in_chat"), -(m.get("pp") or 0)))
    return {
        "lang": lang,
        "team": {
            "id": team.get("id"),
            "name": team.get("name"),
            "short_name": team.get("short_name"),
            "flag_url": team.get("flag_url"),
            "cover_url": team.get("cover_url"),
            "is_open": team.get("is_open"),
            "empty_slots": team.get("empty_slots"),
            "member_count": len(members),
            "statistics": team.get("statistics") or {},
        },
        "members": leading + rest,
    }


PAGE = 20


def roster_page(data: dict, page: int) -> dict:
    members = data["members"]
    leading = [m for m in members if m.get("is_leader")]
    rest = sorted((m for m in members if not m.get("is_leader")), key=lambda m: -(m.get("pp") or 0))
    for place, member in enumerate(rest, start=1):
        member["place"] = place
    ordered = leading + rest
    pages = max(1, -(-len(ordered) // PAGE))
    page = max(0, min(page, pages - 1))
    return dict(data, members=ordered[page * PAGE:(page + 1) * PAGE], page=page, pages=pages,
                first=page * PAGE + 1, total=len(ordered))
