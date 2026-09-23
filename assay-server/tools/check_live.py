import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://osu.ppy.sh/api/v2"

MAPS = (1494828, 1355247, 898576, 5067244, 5114204, 2983479, 4606518, 2477065, 5458625, 3441410)
MOD_SETS = ((), ("HD",), ("HR",), ("DT",), ("HD", "DT"), ("HD", "FL"), ("EZ",))


def call(url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
        **(headers or {}),
    })
    with urllib.request.urlopen(request, timeout=60) as reply:
        return json.load(reply)


def token():
    return call("https://osu.ppy.sh/oauth/token", {
        "client_id": int(os.environ["OSU_CLIENT_ID"]),
        "client_secret": os.environ["OSU_CLIENT_SECRET"],
        "grant_type": "client_credentials",
        "scope": "public",
    })["access_token"]


def main():
    parser = argparse.ArgumentParser(description="compare the assay service with what osu! reports right now")
    parser.add_argument("--assay", default=os.getenv("ASSAY_URL", "http://127.0.0.1:5077"))
    parser.add_argument("--stars", type=float, default=0.002, help="allowed relative star rating difference")
    parser.add_argument("--pp", type=float, default=0.002, help="allowed relative pp difference")
    parser.add_argument("--scores", type=int, default=10, help="leaderboard scores to check per map")
    options = parser.parse_args()

    osu = {"Authorization": f"Bearer {token()}", "x-api-version": "20220705"}
    assay = {"Authorization": f"Bearer {os.environ['ASSAY_TOKEN']}"} if os.getenv("ASSAY_TOKEN") else {}

    off, checked = [], 0
    for beatmap_id in MAPS:
        checksum = call(f"{API}/beatmaps/{beatmap_id}", headers=osu).get("checksum")
        for mods in MOD_SETS:
            theirs = call(f"{API}/beatmaps/{beatmap_id}/attributes", {"mods": list(mods), "ruleset_id": 0}, osu)["attributes"]["star_rating"]
            ours = call(f"{options.assay}/v1/beatmap", {"beatmap_id": beatmap_id, "checksum": checksum, "mods": list(mods)}, assay)["star_rating"]
            checked += 1
            if abs(ours - theirs) > options.stars * theirs:
                off.append(f"{beatmap_id} {''.join(mods) or 'NM'}: {ours:.4f}* here, {theirs:.4f}* on osu!")

        scores = call(f"{API}/beatmaps/{beatmap_id}/scores?mode=osu&limit={options.scores}", headers=osu).get("scores", [])
        for score in scores:
            if not score.get("pp"):
                continue
            body = {
                "beatmap_id": beatmap_id,
                "checksum": checksum,
                "mods": [{"acronym": m["acronym"], "settings": m.get("settings") or {}} for m in score["mods"]],
                "statistics": score["statistics"],
                "accuracy": score["accuracy"],
                "max_combo": score["max_combo"],
                "legacy_total_score": score.get("legacy_total_score") or None,
                "is_legacy": bool(score.get("legacy_score_id")),
            }
            ours = call(f"{options.assay}/v1/score", body, assay)["pp"]
            checked += 1
            if abs(ours - score["pp"]) > options.pp * score["pp"]:
                off.append(f"score {score['id']} on {beatmap_id}: {ours:.2f}pp here, {score['pp']:.2f}pp on osu!")

    health = call(f"{options.assay}/health")
    print(f"assay on osu! {health['osu_version']}: {checked} figures checked, {len(off)} differ")
    for line in off:
        print("  " + line)
    return 1 if off else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as error:
        print(f"{error.url}: HTTP {error.code} {error.read()[:300]!r}", file=sys.stderr)
        sys.exit(2)
