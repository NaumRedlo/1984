"""Fill the corpus's blind spot from ppy's own calculator.

`pp_corpus.py` builds the corpus from the attributes endpoint, which answers
with nine numbers. The formula needs four more that it never returns:

    aim_top_weighted_slider_factor    speed_top_weighted_slider_factor
    reading_difficulty                reading_difficult_note_count

`reading_difficulty` is the one that stings — the reading skill is why Hidden
moves the star rating now, and the endpoint serves ratings computed with it
while keeping its attributes to itself.

osu-tools is ppy's own command-line wrapper around the same calculators, and it
prints all sixteen. It is MIT, like `ppy/osu` itself, so this is a licence
question with a boring answer.

Nothing is borrowed but the numbers. This runs once, by hand, on a machine with
.NET; what lands in the repository is JSON. The server never learns that .NET
exists.

    brew install dotnet
    git clone --depth 1 --recurse-submodules https://github.com/ppy/osu-tools
    cd osu-tools && dotnet build PerformanceCalculator -c Release
    python scripts/pp_corpus_tools.py --dll <path-to>/PerformanceCalculator.dll

The build targets net8.0 and homebrew ships .NET 10, so this sets
`DOTNET_ROLL_FORWARD=Major` rather than asking for a second runtime.

# Whether the two sources agree

They must, and this checks rather than assumes: the endpoint and osu-tools share
nine fields, and every one is compared before anything is merged. A disagreement
would mean the tool is built against a different version of the calculator than
the one the site is serving, which would make its four extra fields worse than
useless — they would be right about a formula nobody is running.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pp_corpus import MOD_SETS  # noqa: E402  — the same sets, by construction

# What osu-tools knows and the endpoint does not.
EXTRA_FIELDS = (
    "aim_top_weighted_slider_factor",
    "speed_top_weighted_slider_factor",
    "reading_difficulty",
    "reading_difficult_note_count",
    # Only present when Flashlight is on, which is why it is merged rather than
    # expected: the endpoint never returns it and the tool only reports it where
    # it means anything.
    "flashlight_difficulty",
    # Not difficulty attributes at all, but the performance side needs them and
    # nothing else offers them: the legacy score simulator is built on these.
    "nested_score_per_object",
    "legacy_score_base_multiplier",
    "maximum_legacy_combo_score",
)

# How closely the two sources have to agree on what they both report, as a
# fraction of the value rather than as an absolute.
#
# Relative because the endpoint answers in single precision and osu-tools in
# double, which is visible the moment you look: it reports a speed note count of
# `1708.3800048828125` and a strain count of `147.58599853515625`, and those are
# the float32 nearest to 1708.38 and 147.586. Comparing absolutely called that a
# disagreement on every field in the hundreds.
#
# A hundred-thousandth admits that rounding — the worst pair in the corpus
# differs by two parts in a million — while still catching what this is for. A
# tool built against a different version of the calculator disagrees in the
# second or third figure, not the seventh.
TOLERANCE = 1e-5


def run(dll: Path, maps: Path, mods: tuple[str, ...]) -> dict[int, dict]:
    """Every map in `maps`, under `mods`, as beatmap id to attributes."""
    args = ["dotnet", str(dll), "difficulty", str(maps), "-j"]
    for mod in mods:
        args += ["-m", mod.lower()]
    env = dict(os.environ)
    env.setdefault("DOTNET_ROOT", "/opt/homebrew/opt/dotnet/libexec")
    # The tool targets net8.0; roll forward rather than install a second runtime.
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
                        default=Path("dossier/crates/dossier-assay/corpus"))
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
                # The endpoint refused this pair; take the tool's word whole.
                entry["attributes"][key] = attributes
                added += 1
                continue

            # Both sources, on every field they share.
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
