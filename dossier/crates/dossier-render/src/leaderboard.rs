//! Who else has played this map, down the left of the frame.
//!
//! osu! puts its scoreboard there and so does this, for the same reason: the
//! left third of a playfield is the emptiest part of it, and a list that has to
//! be readable for four minutes cannot sit where the notes are.
//!
//! The point of drawing it here rather than pasting an image over the video is
//! that it **moves**. The player's own row carries the score the engine is
//! computing frame by frame, the list is sorted at every frame, and a row that
//! passes another passes it on screen. A static scoreboard is a caption; this is
//! part of the play.
//!
//! What it is *not* is a source of truth about anybody. The rival rows are handed
//! in from outside — the bot knows which chat members are registered and what
//! they scored — and this file neither fetches nor validates them. It draws what
//! it is given, and the only row it has an opinion about is the player's own.

/// One rival's standing on this map.
#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub name: String,
    pub score: u64,
    /// Percent, as a player reads it. `None` when whoever supplied the row did
    /// not know — a rank without an accuracy is still worth showing.
    pub accuracy: Option<f64>,
}

/// The rivals, and where the player sits among them.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Leaderboard {
    /// Everyone but the player being rendered.
    pub rivals: Vec<Entry>,
    /// What to call the player's own row.
    pub player: String,
}

impl Leaderboard {
    /// Parse `name<TAB>score[<TAB>accuracy]` a line at a time.
    ///
    /// Tab-separated because a player name can contain almost anything else —
    /// spaces certainly, commas often — and a format that can be broken by a
    /// legal username is a format that will be.
    ///
    /// A malformed line is skipped rather than fatal. This decorates a render;
    /// refusing to draw four minutes of video over one bad row would be the
    /// wrong trade, and the row's absence is visible in the list itself.
    pub fn parse(text: &str, player: &str) -> Self {
        let mut rivals = Vec::new();
        for line in text.lines() {
            let line = line.trim_end_matches(['\r', '\n']);
            if line.trim().is_empty() || line.starts_with('#') {
                continue;
            }
            let mut fields = line.split('\t');
            let (Some(name), Some(score)) = (fields.next(), fields.next()) else {
                continue;
            };
            let Ok(score) = score.trim().parse::<u64>() else {
                continue;
            };
            // The player's own row is never taken from the file: it is computed.
            // A stale copy of it would sit beside the live one and disagree.
            if name.eq_ignore_ascii_case(player) {
                continue;
            }
            rivals.push(Entry {
                name: name.to_owned(),
                score,
                accuracy: fields.next().and_then(|a| a.trim().parse().ok()),
            });
        }
        Self {
            rivals,
            player: player.to_owned(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.rivals.is_empty()
    }

    /// The standings at this instant, best first, with the player's live score
    /// slotted in.
    ///
    /// Ties go to the rival. Two scores level is a moment the player is *about*
    /// to pass somebody, and showing them already ahead of it reads as a place
    /// they have not earned yet.
    pub fn standings(&self, player_score: u64) -> Vec<(Entry, bool)> {
        let mut rows: Vec<(Entry, bool)> = self
            .rivals
            .iter()
            .cloned()
            .map(|entry| (entry, false))
            .collect();
        rows.push((
            Entry {
                name: self.player.clone(),
                score: player_score,
                accuracy: None,
            },
            true,
        ));
        // Stable, so rivals level with each other keep the order they arrived
        // in — whatever supplied them had a reason for it, and reshuffling
        // equal rows every frame would make the list twitch.
        rows.sort_by_key(|row| std::cmp::Reverse(row.0.score));
        rows
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_row_is_a_name_a_score_and_maybe_an_accuracy() {
        let board = Leaderboard::parse("mrekk\t12345678\t99.21\nsw1t\t900\n", "me");
        assert_eq!(board.rivals.len(), 2);
        assert_eq!(board.rivals[0].name, "mrekk");
        assert_eq!(board.rivals[0].accuracy, Some(99.21));
        assert_eq!(board.rivals[1].accuracy, None, "an unknown accuracy is fine");
    }

    #[test]
    fn a_name_with_spaces_survives() {
        // Which is why the format is tabs. Splitting on whitespace would turn
        // one player into two columns.
        let board = Leaderboard::parse("Uika Misumi\t500\n", "me");
        assert_eq!(board.rivals[0].name, "Uika Misumi");
    }

    #[test]
    fn a_bad_row_is_dropped_and_the_rest_still_draw() {
        let board = Leaderboard::parse("good\t10\nnonsense\nbad\tnotanumber\nalso good\t20\n", "me");
        assert_eq!(board.rivals.len(), 2);
    }

    #[test]
    fn the_players_own_row_is_never_taken_from_the_file() {
        // It is computed live. A copy from the file would sit beside it and
        // disagree with it, which is worse than not having it.
        let board = Leaderboard::parse("Me\t999999\nsomebody\t10\n", "me");
        assert_eq!(board.rivals.len(), 1);
        assert_eq!(board.rivals[0].name, "somebody");
    }

    #[test]
    fn the_player_climbs_as_the_score_grows() {
        let board = Leaderboard::parse("a\t300\nb\t200\nc\t100\n", "me");
        let names = |score| {
            board
                .standings(score)
                .into_iter()
                .map(|(e, _)| e.name)
                .collect::<Vec<_>>()
        };
        assert_eq!(names(0), ["a", "b", "c", "me"]);
        assert_eq!(names(150), ["a", "b", "me", "c"]);
        assert_eq!(names(1000), ["me", "a", "b", "c"]);
    }

    #[test]
    fn a_tie_leaves_the_player_behind() {
        // Level is the moment before passing, not the moment after.
        let board = Leaderboard::parse("a\t200\n", "me");
        let rows = board.standings(200);
        assert_eq!(rows[0].0.name, "a");
        assert!(rows[1].1, "and the second row is the player's");
    }

    #[test]
    fn the_players_row_is_marked_wherever_it_lands() {
        let board = Leaderboard::parse("a\t300\nb\t100\n", "me");
        for score in [0, 200, 500] {
            let rows = board.standings(score);
            assert_eq!(rows.iter().filter(|(_, mine)| *mine).count(), 1);
        }
    }
}
