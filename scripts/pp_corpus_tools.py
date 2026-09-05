import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pp_corpus import MOD_SETS

EXTRA_FIELDS = (
    "aim_top_weighted_slider_factor",
    "speed_top_weighted_slider_factor",
    "reading_difficulty",
    "reading_difficult_note_count",

    "flashlight_difficulty",

    "nested_score_per_object",
    "legacy_score_base_multiplier",
    "maximum_legacy_combo_score",
)

TOLERANCE = 1e-5

def corpus_dir() -> Path:
    root = os.getenv("DOSSIER_REPO") or str(Path(__file__).resolve().parents[2] / "Dossier")
    return Path(root) / "crates" / "dossier-assay" / "corpus"

def run(dll: Path, maps: Path, mods: tuple[str, ...]) -> dict[int, dict]:
    args = ["dotnet", str(dll), "difficulty", str(maps), "-j"]
    for mod in mods:
        args += ["-m", mod.lower()]
    env = dict(os.environ)
    env.setdefault("DOTNET_ROOT", "/opt/homebrew/opt/dotnet/libexec")

    env.setdefault("DOTNET_ROLL_FORWARD", "Major")

    done = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    if done.returncode != 0:
        raise SystemExit(f"osu-tools вернул {done.returncode}:\n{done.stderr[:2000]}")
    payload = json.loads(done.stdout)
    for problem in payload.get("errors") or []:
        print(f"  ! {problem}")
    return {
        result["beatmap_id"]: result["attributes"]
        for result in payload.get("results") or []
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path, required=True,
                        help="путь к PerformanceCalculator.dll")
    parser.add_argument("--corpus", type=Path,
                        default=corpus_dir())
    args = parser.parse_args()

    expected_path = args.corpus / "expected.json"
    corpus = json.loads(expected_path.read_text())
    by_id = {entry["beatmap_id"]: entry for entry in corpus["maps"]}

    disagreements: list[str] = []
    added = 0

    for mods in MOD_SETS:
        key = "".join(mods) or "NM"
        found = run(args.dll, args.corpus / "maps", mods)
        for beatmap_id, attributes in found.items():
            entry = by_id.get(beatmap_id)
            if entry is None:
                continue
            theirs = entry["attributes"].get(key)
            if theirs is None:

                entry["attributes"][key] = attributes
                added += 1
                continue

            for field, value in attributes.items():
                if field not in theirs:
                    continue
                mine, ours = float(theirs[field]), float(value)
                scale = max(abs(mine), abs(ours), 1e-9)
                if abs(mine - ours) / scale > TOLERANCE:
                    disagreements.append(
                        f"  {beatmap_id} {key} {field}: эндпоинт {mine}, "
                        f"osu-tools {ours} ({abs(mine - ours) / scale:.2e})"
                    )
            for field in EXTRA_FIELDS:
                if field in attributes:
                    theirs[field] = attributes[field]
                    added += 1
        print(f"  {key}: {len(found)} карт")

    if disagreements:
        print("\nдва источника разошлись — сливать нельзя:")
        for line in disagreements[:20]:
            print(line)
        raise SystemExit(
            "osu-tools собран против другой версии калькулятора, чем та, что "
            "отдаёт сайт. Обнови подмодуль osu! и пересобери."
        )

    corpus["source"] = (
        "osu! API v2 /beatmaps/{id}/attributes, дополнено ppy/osu-tools "
        "(поля, которых эндпоинт не отдаёт)"
    )
    expected_path.write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n")
    print(f"\nдописано {added} полей, два источника сошлись на всех общих")

if __name__ == "__main__":
    main()
