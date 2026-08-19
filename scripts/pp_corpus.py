"""Build the corpus the pp calculator is checked against.

The calculator being written in `dossier/crates/dossier-pp` is a port of ppy's
own difficulty and performance code, and a port is only worth having if it can
be shown to agree with what it was ported from. ppy will tell us: the
attributes endpoint answers with the official difficulty attributes for any map
with any mods, and every ranked score carries the official pp. So the corpus is
their answers, written down.

That gives two things the port needs and could not otherwise have:

- **A field-by-field oracle.** The endpoint returns eight numbers beside the
  star rating — aim, speed, the slider factor, the strain counts. A skill can
  be checked the moment it is written, against the figure it is supposed to
  produce, instead of waiting for a star rating that is "about right".

- **A rebalance alarm.** ppy changes these formulas several times a year and
  says so nowhere we would notice. Rerun this, and any figure that moved is a
  figure they changed: the corpus is regenerated from the source, so a diff on
  it is a diff on their arithmetic.

The maps are committed beside the expected numbers, because a test that
downloads is a test that fails on a train. They are picked for spread rather
than for fame — see `--from-user`, which takes them off a real top hundred and
so lands on the range of things people actually play.

    python scripts/pp_corpus.py --from-user NaumRedlo --maps 10

Rerunning overwrites the expected numbers and leaves the maps alone, which is
the rebalance check: `git diff` on the corpus is what ppy changed.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API = "https://osu.ppy.sh/api/v2"

# What to ask about each map. Every mod that moves the rating gets a turn on its
# own, so a wrong one can be told from a wrong pair, and then the combinations
# people actually play. TD is in because it changes the figure and nothing else
# in the codebase ever thinks about it.
MOD_SETS: tuple[tuple[str, ...], ...] = (
    (), ("HD",), ("HR",), ("DT",), ("HT",), ("EZ",), ("FL",), ("TD",),
    ("HD", "HR"), ("HD", "DT"), ("HR", "DT"), ("HD", "HR", "DT"),
    ("EZ", "DT"), ("HD", "FL"), ("NC",),
)


async def _token(session: aiohttp.ClientSession) -> str:
    async with session.post("https://osu.ppy.sh/oauth/token", json={
        "client_id": int(os.environ["OSU_CLIENT_ID"]),
        "client_secret": os.environ["OSU_CLIENT_SECRET"],
        "grant_type": "client_credentials",
        "scope": "public",
    }) as reply:
        reply.raise_for_status()
        return (await reply.json())["access_token"]


async def _maps_from_user(session, headers, username: str, wanted: int) -> list[int]:
    """Distinct beatmaps off somebody's top hundred.

    A real top hundred spreads over the difficulties and mod sets that get
    played, which is a better corpus than a list of famous maps: those are all
    long, dense and similar.
    """
    async with session.get(f"{API}/users/{username}/osu", headers=headers) as reply:
        reply.raise_for_status()
        user_id = (await reply.json())["id"]
    async with session.get(f"{API}/users/{user_id}/scores/best", headers=headers,
                           params={"limit": 100, "mode": "osu"}) as reply:
        reply.raise_for_status()
        scores = await reply.json()

    seen: list[int] = []
    for score in scores:
        beatmap_id = (score.get("beatmap") or {}).get("id")
        if beatmap_id and beatmap_id not in seen:
            seen.append(beatmap_id)
        if len(seen) >= wanted:
            break
    return seen


async def _attributes(session, headers, beatmap_id: int, mods: tuple[str, ...]) -> dict | None:
    async with session.post(f"{API}/beatmaps/{beatmap_id}/attributes", headers=headers,
                            json={"mods": list(mods), "ruleset_id": 0}) as reply:
        if reply.status != 200:
            return None
        return (await reply.json()).get("attributes")


async def _beatmap(session, headers, beatmap_id: int) -> dict | None:
    async with session.get(f"{API}/beatmaps/{beatmap_id}", headers=headers) as reply:
        if reply.status != 200:
            return None
        return await reply.json()


async def build(out: Path, beatmap_ids: list[int]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "maps").mkdir(exist_ok=True)

    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {await _token(session)}"}
        entries = []
        for beatmap_id in beatmap_ids:
            meta = await _beatmap(session, headers, beatmap_id)
            if not meta:
                print(f"  {beatmap_id}: не отвечает, пропускаю")
                continue

            osu_path = out / "maps" / f"{beatmap_id}.osu"
            if not osu_path.exists():
                async with session.get(f"https://osu.ppy.sh/osu/{beatmap_id}") as reply:
                    body = await reply.read()
                if len(body) < 50:
                    print(f"  {beatmap_id}: файл карты пустой, пропускаю")
                    continue
                osu_path.write_bytes(body)

            per_mods = {}
            for mods in MOD_SETS:
                attrs = await _attributes(session, headers, beatmap_id, mods)
                if attrs:
                    per_mods["".join(mods) or "NM"] = attrs

            entries.append({
                "beatmap_id": beatmap_id,
                "version": meta.get("version"),
                "title": (meta.get("beatmapset") or {}).get("title"),
                # The map's own numbers, which the difficulty calculation needs
                # and which are not in the attributes reply.
                "cs": meta.get("cs"), "ar": meta.get("ar"),
                "od": meta.get("accuracy"), "hp": meta.get("drain"),
                "count_circles": meta.get("count_circles"),
                "count_sliders": meta.get("count_sliders"),
                "count_spinners": meta.get("count_spinners"),
                "attributes": per_mods,
            })
            print(f"  {beatmap_id}: {len(per_mods)} наборов модов — "
                  f"{(meta.get('beatmapset') or {}).get('title', '?')[:40]}")

    (out / "expected.json").write_text(
        json.dumps({"source": "osu! API v2 /beatmaps/{id}/attributes",
                    "maps": entries}, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"\n{len(entries)} карт записано в {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=Path("dossier/crates/dossier-pp/corpus"))
    parser.add_argument("--from-user", default="NaumRedlo",
                        help="чей топ взять за источник карт")
    parser.add_argument("--maps", type=int, default=10, help="сколько карт")
    parser.add_argument("--ids", default="", help="явный список id через запятую")
    args = parser.parse_args()

    async def run():
        if args.ids:
            ids = [int(part) for part in args.ids.split(",") if part.strip()]
        else:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {await _token(session)}"}
                ids = await _maps_from_user(session, headers, args.from_user, args.maps)
        print(f"карт: {len(ids)}")
        await build(args.out, ids)

    asyncio.run(run())


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
