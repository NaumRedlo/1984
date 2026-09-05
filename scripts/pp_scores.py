import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PLAYS: tuple[tuple[str, float, int, float | None], ...] = (
    ("perfect",        100.0,  0, None),
    ("near perfect",    99.2,  0, None),
    ("good",            97.5,  0, None),
    ("mediocre",        94.0,  0, None),
    ("bad",             88.0,  0, None),
    ("one miss",        98.6,  1, 0.85),
    ("a few misses",    96.0,  5, 0.60),
    ("many misses",     91.0, 25, 0.35),
    ("slider break",    99.0,  0, 0.50),
    ("early quit",      97.0,  2, 0.10),
)

MOD_SETS: tuple[tuple[str, ...], ...] = ((), ("HD",), ("HR",), ("DT",), ("HD", "DT"), ("EZ",))

CLASSIC_MOD_SETS: tuple[tuple[str, ...], ...] = ((), ("HD",), ("HR",), ("DT",))

def corpus_dir() -> Path:
    root = os.getenv("DOSSIER_REPO") or str(Path(__file__).resolve().parents[2] / "Dossier")
    return Path(root) / "crates" / "dossier-assay" / "corpus"

def simulate(dll: Path, beatmap: Path, mods: tuple[str, ...], accuracy: float,
             misses: int, combo: int | None, total: int | None = None) -> dict | None:
    args = ["dotnet", str(dll), "simulate", "osu", str(beatmap), "-j",
            "-a", str(accuracy), "-X", str(misses)]
    if combo is not None:
        args += ["--combo", str(combo)]
    if total is not None:
        args += ["-l", str(total)]
    for mod in mods:
        args += ["-m", mod.lower()]

    env = dict(os.environ)
    env.setdefault("DOTNET_ROOT", "/opt/homebrew/opt/dotnet/libexec")
    env.setdefault("DOTNET_ROLL_FORWARD", "Major")

    env.setdefault("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT", "1")
    done = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    if done.returncode != 0:
        print(f"  ! {done.stderr.strip()[:200]}")
        return None
    return json.loads(done.stdout)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--corpus", type=Path,
                        default=corpus_dir())
    parser.add_argument("--maps", type=int, default=4,
                        help="сколько карт корпуса взять (плей × мод × карта растёт быстро)")
    args = parser.parse_args()

    difficulty = json.loads((args.corpus / "expected.json").read_text())
    chosen = difficulty["maps"][: args.maps]

    out = []
    for entry in chosen:
        beatmap_id = entry["beatmap_id"]
        path = args.corpus / "maps" / f"{beatmap_id}.osu"

        max_combo = (entry["attributes"].get("NM") or {}).get("max_combo")
        if not max_combo:
            continue

        classic = [mods + ("CL",) for mods in CLASSIC_MOD_SETS]
        for mods in tuple(MOD_SETS) + tuple(classic):
            key = "".join(mods) or "NM"

            without_classic = "".join(m for m in mods if m != "CL") or "NM"
            ceiling = (entry["attributes"].get(without_classic) or {}).get(
                "maximum_legacy_combo_score"
            )
            for name, accuracy, misses, share in PLAYS:
                combo = None if share is None else max(1, int(max_combo * share))

                total = None
                if "CL" in mods and ceiling:
                    reached = (combo or max_combo) / max_combo
                    total = int(ceiling * reached * reached * accuracy / 100)

                result = simulate(args.dll, path, mods, accuracy, misses, combo, total)
                if not result:
                    continue
                score = result.get("score") or {}
                out.append({
                    "beatmap_id": beatmap_id,
                    "mods": key,
                    "play": name,
                    "accuracy": score.get("accuracy"),
                    "combo": score.get("combo"),
                    "statistics": score.get("statistics"),
                    "legacy_total_score": score.get("legacy_total_score"),
                    "pp": result.get("performance_attributes", {}).get("pp"),
                    "performance": result.get("performance_attributes"),
                })
        print(f"  {beatmap_id}: {len(out)} плеев всего")

    (args.corpus / "scores.json").write_text(
        json.dumps({"source": "ppy/osu-tools simulate", "scores": out},
                   indent=2, ensure_ascii=False) + "\n"
    )
    print(f"\n{len(out)} плеев записано")

if __name__ == "__main__":
    main()
