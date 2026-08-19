"""The score corpus: what real plays are worth, from ppy's own calculator.

The difficulty side is graded against attributes, and the performance side
cannot be — a pp figure is a fact about a *play*, not about a map. So this
builds the other corpus: plays over the maps already in `corpus/maps`, each with
its official pp from `osu-tools simulate`.

Made up rather than collected, and deliberately. Real scores would be more
convincing and are far worse coverage: people do not miss on purpose, so a
hundred top plays are a hundred near-perfect ones, and the parts of the formula
that only wake up on a broken play — the effective miss count, the estimated
slider breaks, the combo-based penalties — would never be tested at all. These
plays are chosen to hit those instead: full combos, quiet misses, ruinous
misses, low combo with no misses at all (a slider break), and accuracies from
sublime to bad.

    python scripts/pp_scores.py --dll <path-to>/PerformanceCalculator.dll

Same rules as `pp_corpus_tools.py`: run by hand, on a machine with .NET, and
what lands in the repository is JSON.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# How a play is described: accuracy, misses, and what share of the map's combo
# was reached. `None` for combo means "the whole map", which is what an unbroken
# play gets.
#
# The awkward cases are the point. A play with no misses but half the combo is a
# slider break, and the calculator has to guess at how many — that guess is one
# of the newest and least exercised parts of the formula. A play with many
# misses on a short map runs into the clamps.
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

# The mod sets to ask about. Fewer than the difficulty corpus uses, because this
# is a product of plays and mods and maps and it grows quickly.
MOD_SETS: tuple[tuple[str, ...], ...] = ((), ("HD",), ("HR",), ("DT",), ("HD", "DT"), ("EZ",))

# And the same again under Classic, which is the *other* half of the calculator.
#
# A classic score is scored the old way: a slider's head carries no accuracy, a
# dropped tail is invisible, and nothing records where combo broke. Everything
# the calculator does about that — estimating dropped ends, estimating slider
# breaks, and reading the miss count back out of a ScoreV1 total — runs only
# here, and none of it was exercised until these were added.
CLASSIC_MOD_SETS: tuple[tuple[str, ...], ...] = ((), ("HD",), ("HR",), ("DT",))


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
    # Without this the tool reads "97.5" by the machine's locale and refuses it
    # wherever a comma is the decimal separator.
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
                        default=Path("dossier/crates/dossier-assay/corpus"))
    parser.add_argument("--maps", type=int, default=4,
                        help="сколько карт корпуса взять (плей × мод × карта растёт быстро)")
    args = parser.parse_args()

    difficulty = json.loads((args.corpus / "expected.json").read_text())
    chosen = difficulty["maps"][: args.maps]

    out = []
    for entry in chosen:
        beatmap_id = entry["beatmap_id"]
        path = args.corpus / "maps" / f"{beatmap_id}.osu"
        # The map's own maximum, so a share of it can be turned into a number.
        max_combo = (entry["attributes"].get("NM") or {}).get("max_combo")
        if not max_combo:
            continue

        classic = [mods + ("CL",) for mods in CLASSIC_MOD_SETS]
        for mods in tuple(MOD_SETS) + tuple(classic):
            key = "".join(mods) or "NM"
            # The map's greatest ScoreV1 combo portion under these mods, which
            # is what a plausible total is scaled from.
            without_classic = "".join(m for m in mods if m != "CL") or "NM"
            ceiling = (entry["attributes"].get(without_classic) or {}).get(
                "maximum_legacy_combo_score"
            )
            for name, accuracy, misses, share in PLAYS:
                combo = None if share is None else max(1, int(max_combo * share))

                # A classic score is read out of its ScoreV1 total, so one has
                # to be supplied — `simulate` will not invent it. The figure
                # below is arbitrary but plausible: the map's own maximum combo
                # score scaled by how much of the combo the play reached,
                # squared because that portion grows with the square of combo,
                # and by its accuracy. What matters is not that it is the total
                # the play would really have got, but that both calculators are
                # handed the same one.
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
