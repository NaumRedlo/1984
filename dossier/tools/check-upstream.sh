#!/bin/bash
# Are the files our rules were read from still what we read?
#
# Every quoted block in this engine is a promise about somebody else's source,
# and lazer ships weekly. This refetches each file named in upstream.tsv and
# reports which have moved since the hash was taken.
#
#   tools/check-upstream.sh          check, and exit non-zero if anything moved
#   tools/check-upstream.sh --update rewrite the hashes to what is upstream now
#
# A changed file is not a changed rule. It is a prompt to read the diff.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
manifest="$here/upstream.tsv"
update=0
[ "${1:-}" = "--update" ] && update=1

moved=0; checked=0; unreachable=0
out="$(mktemp)"
while IFS=$'\t' read -r repo path sha note; do
    case "$repo" in ''|'#'*) printf '%s\n' "$repo" >> "$out"; continue;; esac
    body="$(curl -sfL --max-time 30 "https://raw.githubusercontent.com/$repo/master/$path")"
    if [ -z "$body" ]; then
        echo "  ?? $repo $path — could not be fetched"
        unreachable=$((unreachable + 1))
        printf '%s\t%s\t%s\t%s\n' "$repo" "$path" "$sha" "$note" >> "$out"
        continue
    fi
    now="$(printf '%s' "$body" | shasum -a 256 | cut -d' ' -f1 | cut -c1-16)"
    checked=$((checked + 1))
    if [ "$sha" = "-" ] || [ "$update" = 1 ]; then
        [ "$sha" != "-" ] && [ "$sha" != "$now" ] && echo "  ~~ $path — updated"
        printf '%s\t%s\t%s\t%s\n' "$repo" "$path" "$now" "$note" >> "$out"
    elif [ "$sha" != "$now" ]; then
        echo "  !! $path"
        echo "        $note"
        echo "        https://github.com/$repo/commits/master/$path"
        moved=$((moved + 1))
        printf '%s\t%s\t%s\t%s\n' "$repo" "$path" "$sha" "$note" >> "$out"
    else
        printf '%s\t%s\t%s\t%s\n' "$repo" "$path" "$sha" "$note" >> "$out"
    fi
done < "$manifest"

if [ "$update" = 1 ] || grep -q '	-	' "$manifest"; then
    mv "$out" "$manifest"
else
    rm -f "$out"
fi

echo
if [ "$moved" -gt 0 ]; then
    echo "$moved of $checked files have moved since they were read."
    echo "Read the diffs, then re-run with --update once the rules are checked."
    exit 1
fi
echo "$checked files unchanged since they were read${unreachable:+, $unreachable unreachable}."
