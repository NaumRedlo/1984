//! What the corpus *is*, written down.
//!
//! For months the corpus was whatever a `find` command happened to match on
//! one machine. That was found out the hard way: twelve replays were sitting
//! in a directory nobody had thought to point it at, and every number this
//! project had published was taken without them. A measurement whose inputs
//! are discovered rather than declared is not reproducible, and a number that
//! cannot be reproduced is not a measurement.
//!
//! So the set is named here, by the one thing about a replay that does not
//! change: the MD5 of the file. Filenames vary, the same replay sits in two
//! folders, a download gets `(2)` appended. The hash does not.
//!
//! The replays themselves are not in the repository and will not be — they are
//! other people's plays. What is here is enough to say whether the set on this
//! machine is the set the numbers were taken from, and to go and fetch the
//! beatmaps for it.
//!
//! Each row also carries what that replay is *expected* to do. A single total
//! held to a ceiling hides trades: two replays can get worse while a third
//! gets better and the sum will not move. Per-replay expectations do not
//! allow that.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::Path;

/// One replay's place in the corpus, and what it is expected to do.
#[derive(Clone, Debug, PartialEq)]
pub struct Expectation {
    /// MD5 of the `.osr` file. The row's identity.
    pub replay_md5: String,
    /// MD5 of the beatmap it names, which is how the map is found or fetched.
    pub beatmap_md5: String,
    /// The beatmap's id, once something has resolved it. Pinned so that
    /// recovering the map later does not depend on a mirror still answering
    /// hash lookups. `None` until `tools/fetch-maps.py` fills it in.
    pub beatmap_id: Option<u32>,
    /// Counts that disagree with the replay's own header, added up.
    pub error: u32,
    /// Our maximum combo less the replay's.
    pub combo: i64,
    /// How far the score is out, as a percentage, where it can be compared.
    pub score: Option<f64>,
    /// Only so a human can read the file. Nothing keys on it.
    pub name: String,
}

impl Expectation {
    /// Is this run worse than what was written down?
    ///
    /// Better is never a failure — it is the point — but it does mean the file
    /// is stale, which `--update-expect` is for.
    pub fn worse_than(&self, error: u32, combo: i64, score: Option<f64>) -> Option<String> {
        if error > self.error {
            return Some(format!("count error {} → {error}", self.error));
        }
        if combo.abs() > self.combo.abs() {
            return Some(format!("combo {:+} → {combo:+}", self.combo));
        }
        // A hundredth of a per cent of slack, because the score is a float
        // that goes through a division and a rounding on the way here and
        // will not land on the same bits between builds.
        match (self.score, score) {
            (Some(was), Some(now)) if now.abs() > was.abs() + 0.01 => {
                Some(format!("score {was:+.2}% → {now:+.2}%"))
            }
            // A score that used to be comparable and no longer is means the
            // engine stopped being able to read something it could read
            // before. That is a regression even though no number got bigger.
            (Some(was), None) => Some(format!("score {was:+.2}% → not comparable")),
            _ => None,
        }
    }
}

/// Every row, keyed by the replay's hash.
pub fn read(path: &Path) -> Result<BTreeMap<String, Expectation>, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut rows = BTreeMap::new();
    for (number, line) in text.lines().enumerate() {
        // Line endings only. `trim_end` would take the tab off a row whose
        // last field is empty and leave six fields where there are seven.
        let line = line.trim_end_matches(['\r', '\n']);
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<&str> = line.split('\t').collect();
        let [replay_md5, beatmap_md5, beatmap_id, error, combo, score, name] = fields[..] else {
            return Err(format!(
                "{}:{}: expected 7 tab-separated fields, found {}",
                path.display(),
                number + 1,
                fields.len()
            ));
        };
        let at = |what: &str| format!("{}:{}: bad {what}", path.display(), number + 1);
        rows.insert(
            replay_md5.to_owned(),
            Expectation {
                replay_md5: replay_md5.to_owned(),
                beatmap_md5: beatmap_md5.to_owned(),
                beatmap_id: match beatmap_id {
                    "-" => None,
                    id => Some(id.parse().map_err(|_| at("beatmap id"))?),
                },
                error: error.parse().map_err(|_| at("count error"))?,
                combo: combo.parse().map_err(|_| at("combo"))?,
                score: match score {
                    "-" => None,
                    off => Some(off.parse().map_err(|_| at("score"))?),
                },
                name: name.to_owned(),
            },
        );
    }
    Ok(rows)
}

/// What the manifest becomes after a run, and how many rows it lost.
///
/// `measured` is what this run judged; `on_disk` is every replay hash it found,
/// which is not the same set — a replay can be sitting right here and still
/// fail to judge, a missing map being the usual way.
///
/// Rows for replays the run did not see survive, because the corpus is a list
/// of replays this machine may or may not be holding today: they live outside
/// the repository, and a run over the few that are here is not news that the
/// rest have ceased to exist. `prune` is how a row actually leaves, for a
/// replay dropped from the corpus rather than merely absent from this disk —
/// and it spares anything `on_disk`, judged or not.
///
/// A pinned beatmap id survives a re-measurement. Nothing in a run resolves
/// one, so losing it would quietly cost the map its way back.
pub fn after_run(
    was: &BTreeMap<String, Expectation>,
    measured: Vec<Expectation>,
    on_disk: &std::collections::BTreeSet<String>,
    prune: bool,
) -> (BTreeMap<String, Expectation>, usize) {
    let mut rows = was.clone();
    for mut fresh in measured {
        fresh.beatmap_id = fresh
            .beatmap_id
            .or_else(|| was.get(&fresh.replay_md5).and_then(|old| old.beatmap_id));
        rows.insert(fresh.replay_md5.clone(), fresh);
    }
    let dropped = if prune {
        let before = rows.len();
        rows.retain(|md5, _| on_disk.contains(md5));
        before - rows.len()
    } else {
        0
    };
    (rows, dropped)
}

/// Write the whole manifest out, replacing whatever was there.
///
/// The caller decides what the rows are: `corpus --update-expect` merges what
/// it measured into what it read, so that a run over part of the corpus does
/// not delete the rest of it.
pub fn write(path: &Path, rows: &BTreeMap<String, Expectation>) -> Result<(), String> {
    let mut out = String::from(
        "# The corpus: which replays it is made of, and what each one does.\n\
         #\n\
         # Written by `dossier corpus --expect <this file> --update-expect`, and\n\
         # the beatmap ids by `tools/fetch-maps.py --manifest <this file>`. Do not\n\
         # edit by hand — a row that disagrees with what the engine measures is\n\
         # worse than no row at all.\n\
         #\n\
         # replay_md5\tbeatmap_md5\tbeatmap_id\terror\tcombo\tscore\tname\n",
    );
    for row in rows.values() {
        let id = row
            .beatmap_id
            .map_or_else(|| "-".to_owned(), |id| id.to_string());
        let score = row
            .score
            .map_or_else(|| "-".to_owned(), |off| format!("{off:.2}"));
        let _ = writeln!(
            out,
            "{}\t{}\t{id}\t{}\t{}\t{score}\t{}",
            row.replay_md5, row.beatmap_md5, row.error, row.combo, row.name
        );
    }
    // Beside the target and moved, so an interrupted write never leaves half a
    // corpus definition where a whole one is expected.
    let temporary = path.with_extension("tsv.part");
    std::fs::write(&temporary, out).map_err(|e| format!("{}: {e}", temporary.display()))?;
    std::fs::rename(&temporary, path).map_err(|e| format!("{}: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row() -> Expectation {
        Expectation {
            replay_md5: "a".repeat(32),
            beatmap_md5: "b".repeat(32),
            beatmap_id: Some(12345),
            error: 4,
            combo: -1,
            score: Some(0.12),
            name: "somebody — a map".to_owned(),
        }
    }

    #[test]
    fn a_run_that_matches_is_not_worse() {
        assert_eq!(row().worse_than(4, -1, Some(0.12)), None);
    }

    #[test]
    fn improving_is_never_a_failure() {
        assert_eq!(row().worse_than(0, 0, Some(0.0)), None);
    }

    #[test]
    fn more_count_error_is_worse() {
        assert!(row().worse_than(5, -1, Some(0.12)).is_some());
    }

    #[test]
    fn combo_is_judged_by_distance_not_sign() {
        // −1 → +1 is the same distance and not a regression; −1 → −2 is.
        assert_eq!(row().worse_than(4, 1, Some(0.12)), None);
        assert!(row().worse_than(4, -2, Some(0.12)).is_some());
    }

    #[test]
    fn the_score_gets_a_hundredth_of_slack() {
        // It survives a float landing differently between builds, and does not
        // survive an actual move.
        assert_eq!(row().worse_than(4, -1, Some(0.125)), None);
        assert!(row().worse_than(4, -1, Some(0.30)).is_some());
    }

    #[test]
    fn losing_the_ability_to_compare_a_score_is_a_regression() {
        // No number got bigger, and something still stopped working.
        assert!(row().worse_than(4, -1, None).is_some());
    }

    /// The corpus as the file has it: the row above, and one for a replay that
    /// is not on this machine today.
    fn was() -> BTreeMap<String, Expectation> {
        let elsewhere = Expectation {
            replay_md5: "e".repeat(32),
            name: "a replay on the other machine".to_owned(),
            ..row()
        };
        BTreeMap::from([
            (row().replay_md5, row()),
            (elsewhere.replay_md5.clone(), elsewhere),
        ])
    }

    fn on_disk(hashes: &[&str]) -> std::collections::BTreeSet<String> {
        hashes.iter().map(|h| (*h).to_owned()).collect()
    }

    #[test]
    fn a_partial_run_keeps_the_rows_it_did_not_see() {
        // The whole point: eleven replays on the disk must not delete the
        // other hundred and twenty-three.
        let measured = vec![Expectation {
            error: 0,
            ..row()
        }];
        let (rows, dropped) = after_run(&was(), measured, &on_disk(&[&"a".repeat(32)]), false);
        assert_eq!(rows.len(), 2);
        assert_eq!(dropped, 0);
        assert_eq!(rows[&"e".repeat(32)].name, "a replay on the other machine");
        // …and what it did see is the new measurement, not the old one.
        assert_eq!(rows[&"a".repeat(32)].error, 0);
    }

    #[test]
    fn a_replay_the_file_never_heard_of_is_added() {
        let arrival = Expectation {
            replay_md5: "f".repeat(32),
            beatmap_id: None,
            ..row()
        };
        let (rows, _) = after_run(&was(), vec![arrival], &on_disk(&[&"f".repeat(32)]), false);
        assert_eq!(rows.len(), 3);
        assert!(rows.contains_key(&"f".repeat(32)));
    }

    #[test]
    fn a_pinned_beatmap_id_survives_a_re_measurement() {
        // Nothing in a run resolves an id, so a measured row arrives without
        // one. Taking that at face value would cost the map its way back.
        let measured = vec![Expectation {
            beatmap_id: None,
            ..row()
        }];
        let (rows, _) = after_run(&was(), measured, &on_disk(&[&"a".repeat(32)]), false);
        assert_eq!(rows[&"a".repeat(32)].beatmap_id, Some(12345));
    }

    #[test]
    fn pruning_drops_what_is_not_on_the_disk() {
        let measured = vec![row()];
        let (rows, dropped) = after_run(&was(), measured, &on_disk(&[&"a".repeat(32)]), true);
        assert_eq!(dropped, 1);
        assert_eq!(rows.len(), 1);
        assert!(!rows.contains_key(&"e".repeat(32)));
    }

    #[test]
    fn pruning_spares_a_replay_that_is_here_and_could_not_be_judged() {
        // Present but unjudgeable — no map for it — so it is in `on_disk` and
        // not in `measured`. The replay has not left the corpus, and dropping
        // its row would lose a replay we are holding.
        let (rows, dropped) = after_run(
            &was(),
            Vec::new(),
            &on_disk(&["a".repeat(32).as_str(), "e".repeat(32).as_str()]),
            true,
        );
        assert_eq!(dropped, 0);
        assert_eq!(rows.len(), 2);
    }

    #[test]
    fn a_row_survives_being_written_and_read_back() {
        let directory = std::env::temp_dir().join(format!("dossier-manifest-{}", std::process::id()));
        std::fs::create_dir_all(&directory).expect("a scratch directory");
        let path = directory.join("corpus.tsv");
        let mut rows = BTreeMap::new();
        rows.insert(row().replay_md5.clone(), row());
        // A row with nothing known but the hashes has to survive too — that is
        // every row on the day it is first written.
        let bare = Expectation {
            replay_md5: "c".repeat(32),
            beatmap_id: None,
            score: None,
            name: String::new(),
            ..row()
        };
        rows.insert(bare.replay_md5.clone(), bare);

        write(&path, &rows).expect("it writes");
        assert_eq!(read(&path).expect("it reads back"), rows);
        let _ = std::fs::remove_dir_all(&directory);
    }

    #[test]
    fn a_malformed_row_is_named_rather_than_skipped() {
        let directory = std::env::temp_dir().join(format!("dossier-bad-{}", std::process::id()));
        std::fs::create_dir_all(&directory).expect("a scratch directory");
        let path = directory.join("corpus.tsv");
        std::fs::write(&path, "# fine\nnot\tenough\tfields\n").expect("it writes");
        let error = read(&path).expect_err("three fields is not seven");
        assert!(error.contains(":2:"), "{error}");
        let _ = std::fs::remove_dir_all(&directory);
    }
}
