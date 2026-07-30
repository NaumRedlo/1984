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

/// How long a row takes to slide from its old place to its new one.
pub const MOVE_MS: f64 = 420.0;

/// One rival's standing on this map.
#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub name: String,
    pub score: u64,
    /// Percent, as a player reads it. `None` when whoever supplied the row did
    /// not know — a rank without an accuracy is still worth showing.
    pub accuracy: Option<f64>,
    /// What they played it with.
    ///
    /// Carried because these rows are each player's *best* score on the map,
    /// whatever they used to set it. A twelve-million NM run beside a HardRock
    /// DoubleTime play is a fair comparison of scores and a misleading
    /// comparison of plays, and the mods are what tell the two apart. Empty for
    /// no mods, or when the supplier did not say.
    pub mods: String,
    /// A PNG of this player's avatar, if one was supplied.
    ///
    /// A path rather than the bytes, and PNG rather than whatever osu! serves,
    /// because the engine has one image decoder and no network. Converting is
    /// the caller's job — the bot already has an imaging library and already
    /// caches every avatar it has seen.
    pub avatar: Option<std::path::PathBuf>,
    /// A PNG of their profile cover, to sit behind the row.
    pub cover: Option<std::path::PathBuf>,
}

/// The rivals, and where the player sits among them.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Leaderboard {
    /// Everyone but the player being rendered.
    pub rivals: Vec<Entry>,
    /// What to call the player's own row.
    pub player: String,
    /// Their avatar and cover, supplied the same way as a rival's.
    pub avatar: Option<std::path::PathBuf>,
    pub cover: Option<std::path::PathBuf>,
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
            let accuracy = fields.next().and_then(|a| a.trim().parse().ok());
            let mods = fields.next().unwrap_or_default().trim().to_owned();
            let picture = |field: Option<&str>| {
                field
                    .map(str::trim)
                    .filter(|path| !path.is_empty())
                    .map(std::path::PathBuf::from)
            };
            rivals.push(Entry {
                name: name.to_owned(),
                score,
                accuracy,
                mods,
                avatar: picture(fields.next()),
                cover: picture(fields.next()),
            });
        }
        Self {
            rivals,
            player: player.to_owned(),
            avatar: None,
            cover: None,
        }
    }

    /// The pictures for the player's own row, which no rival line can carry.
    #[must_use]
    pub fn with_own_pictures(
        mut self,
        avatar: Option<std::path::PathBuf>,
        cover: Option<std::path::PathBuf>,
    ) -> Self {
        self.avatar = avatar;
        self.cover = cover;
        self
    }

    pub fn is_empty(&self) -> bool {
        self.rivals.is_empty()
    }

    /// The standings at this instant, and where each row is coming from.
    ///
    /// Rows are returned **worst first**: the list is read upwards, ending on the
    /// best score on the map. A scoreboard that puts the leader at the top is a
    /// table; one that climbs to them is a story, and the player's row rising
    /// through it is the only thing on screen that changes place.
    ///
    /// Ties go to the rival. Two scores level is a moment the player is *about*
    /// to pass somebody, and showing them already ahead of it reads as a place
    /// they have not earned yet.
    ///
    /// `moving` is how far through a place change the row is, from 0 at the
    /// moment it starts to 1 when it has arrived, together with the place it is
    /// coming from. Computed from the score curve rather than remembered between
    /// frames — the player's score at any instant is known in advance, so the
    /// instant they passed each rival is too, and a frame can work out its own
    /// animation without having seen the one before it. That constraint is what
    /// lets frames be drawn in parallel, and it is not negotiable.
    pub fn standings(&self, player_score: u64, limit: usize) -> Vec<Row> {
        let ordered = self.ordered(player_score);
        // Best `limit` of them, and the player always among them: a scoreboard
        // that can hide the play it belongs to is worse than a shorter one.
        let mut kept: Vec<(Entry, bool)> = ordered.iter().take(limit).cloned().collect();
        if !kept.iter().any(|(_, mine)| *mine) {
            if let Some(player) = ordered.iter().find(|(_, mine)| *mine) {
                kept.pop();
                kept.push(player.clone());
            }
        }
        let places: Vec<usize> = kept
            .iter()
            .map(|(entry, mine)| self.place_of(&ordered, entry, *mine))
            .collect();

        // Worst first, so the eye runs up the list to the leader.
        let mut rows: Vec<Row> = kept
            .into_iter()
            .zip(places)
            .map(|((entry, is_player), place)| Row {
                entry,
                is_player,
                place,
                from_place: place,
                moving: 1.0,
            })
            .collect();
        rows.reverse();
        rows
    }

    /// The same, with each row told what it is moving from and how far along.
    pub fn standings_at(&self, track: &dyn ScoreAt, time_ms: f64, limit: usize) -> Vec<Row> {
        let now = track.at(time_ms);
        let mut rows = self.standings(now, limit);
        // When the player last changed place: the most recent rival score they
        // crossed. The curve only rises, so each rival is passed at most once.
        let last_pass = self
            .rivals
            .iter()
            .map(|rival| rival.score)
            .filter(|score| *score < now)
            .map(|score| track.reached(score))
            .fold(f64::NEG_INFINITY, f64::max);
        if !last_pass.is_finite() || time_ms - last_pass > MOVE_MS {
            return rows;
        }
        let progress = (((time_ms - last_pass) / MOVE_MS).clamp(0.0, 1.0)) as f32;
        // Where everybody stood a moment before the pass, so each row knows the
        // place it is leaving rather than only the one it is arriving at.
        let before = self.standings(track.at(last_pass - 1.0), limit);
        for row in &mut rows {
            if let Some(was) = before
                .iter()
                .find(|other| other.is_player == row.is_player && other.entry.name == row.entry.name)
            {
                row.from_place = was.place;
                row.moving = progress;
            }
        }
        rows
    }

    /// Everybody, best first, with the player slotted in.
    fn ordered(&self, player_score: u64) -> Vec<(Entry, bool)> {
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
                // The play being watched. Its mods are already on screen in the
                // corner, and repeating them in its own row would be the one
                // piece of information nobody watching needs.
                mods: String::new(),
                avatar: self.avatar.clone(),
                cover: self.cover.clone(),
            },
            true,
        ));
        // Stable, so rivals level with each other keep the order they arrived
        // in — whatever supplied them had a reason for it, and reshuffling
        // equal rows every frame would make the list twitch.
        rows.sort_by_key(|row| std::cmp::Reverse(row.0.score));
        rows
    }

    fn place_of(&self, ordered: &[(Entry, bool)], entry: &Entry, is_player: bool) -> usize {
        ordered
            .iter()
            .position(|(other, mine)| *mine == is_player && other.name == entry.name)
            .unwrap_or(0)
    }
}

/// One line of the scoreboard, and its movement.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub entry: Entry,
    pub is_player: bool,
    /// Zero-based place, counting from the best score.
    pub place: usize,
    /// The place it is arriving from. Equal to `place` when it is not moving.
    pub from_place: usize,
    /// How far through the move, 0 to 1.
    pub moving: f32,
}

/// The score curve, as much of it as a scoreboard needs.
///
/// A trait rather than the concrete track so this file stays testable without a
/// judged play, and so the renderer can hand in whatever it has.
pub trait ScoreAt {
    /// The score at this instant.
    fn at(&self, time_ms: f64) -> u64;
    /// When the score first reached this value.
    fn reached(&self, score: u64) -> f64;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn board(text: &str) -> Leaderboard {
        Leaderboard::parse(text, "me")
    }

    /// Names in the order they are drawn: worst kept score first, leader last.
    fn drawn(board: &Leaderboard, score: u64, limit: usize) -> Vec<String> {
        board
            .standings(score, limit)
            .into_iter()
            .map(|row| row.entry.name)
            .collect()
    }

    #[test]
    fn a_row_is_a_name_a_score_and_maybe_an_accuracy() {
        let b = board("mrekk\t12345678\t99.21\tHDDT\nsw1t\t900\n");
        assert_eq!(b.rivals.len(), 2);
        assert_eq!(b.rivals[0].name, "mrekk");
        assert_eq!(b.rivals[0].accuracy, Some(99.21));
        assert_eq!(b.rivals[0].mods, "HDDT");
        assert_eq!(b.rivals[1].accuracy, None, "an unknown accuracy is fine");
        assert_eq!(b.rivals[1].mods, "", "and so are unknown mods");
    }

    #[test]
    fn a_row_can_carry_an_avatar_and_a_cover() {
        let b = board("mrekk\t900\t99.00\tHD\t/tmp/a.png\t/tmp/c.png\n");
        assert_eq!(b.rivals[0].avatar.as_deref(), Some(std::path::Path::new("/tmp/a.png")));
        assert_eq!(b.rivals[0].cover.as_deref(), Some(std::path::Path::new("/tmp/c.png")));
        // And an empty field is absent rather than a path to nowhere.
        let bare = board("sw1t\t900\t99.00\tHD\t\t\n");
        assert!(bare.rivals[0].avatar.is_none());
    }

    #[test]
    fn a_name_with_spaces_survives() {
        // Which is why the format is tabs. Splitting on whitespace would turn
        // one player into two columns.
        assert_eq!(board("Uika Misumi\t500\n").rivals[0].name, "Uika Misumi");
    }

    #[test]
    fn a_bad_row_is_dropped_and_the_rest_still_draw() {
        let b = board("good\t10\nnonsense\nbad\tnotanumber\nalso good\t20\n");
        assert_eq!(b.rivals.len(), 2);
    }

    #[test]
    fn the_players_own_row_is_never_taken_from_the_file() {
        // It is computed live. A copy from the file would sit beside it and
        // disagree with it, which is worse than not having it.
        let b = board("Me\t999999\nsomebody\t10\n");
        assert_eq!(b.rivals.len(), 1);
        assert_eq!(b.rivals[0].name, "somebody");
    }

    #[test]
    fn the_board_is_read_upwards_to_the_leader() {
        // A board with the leader on top is a table; one that climbs to them is
        // a story, and the player's row rising through it is the only thing on
        // screen that changes place.
        let b = board("a\t300\nb\t200\nc\t100\n");
        assert_eq!(drawn(&b, 0, 5), ["me", "c", "b", "a"]);
        assert_eq!(drawn(&b, 1000, 5), ["c", "b", "a", "me"]);
    }

    #[test]
    fn the_player_climbs_as_the_score_grows() {
        let b = board("a\t300\nb\t200\nc\t100\n");
        assert_eq!(drawn(&b, 150, 5), ["c", "me", "b", "a"]);
        assert_eq!(drawn(&b, 250, 5), ["c", "b", "me", "a"]);
    }

    #[test]
    fn a_tie_leaves_the_player_behind() {
        // Level is the moment before passing, not the moment after.
        let b = board("a\t200\n");
        let rows = b.standings(200, 5);
        assert!(rows[0].is_player, "the player is drawn first, meaning last place");
        assert_eq!(rows[1].entry.name, "a");
    }

    #[test]
    fn the_board_is_never_longer_than_it_is_allowed_to_be() {
        let b = board("a\t900\nb\t800\nc\t700\nd\t600\ne\t500\nf\t400\ng\t300\n");
        assert_eq!(b.standings(0, 5).len(), 5);
    }

    #[test]
    fn the_player_is_kept_even_when_they_are_nowhere_near_the_top() {
        // A scoreboard that can hide the play it belongs to is worse than a
        // shorter one.
        let b = board("a\t900\nb\t800\nc\t700\nd\t600\ne\t500\nf\t400\n");
        let rows = b.standings(1, 5);
        assert_eq!(rows.len(), 5);
        assert!(rows.iter().any(|row| row.is_player));
        assert!(rows[0].is_player, "and last place is drawn first");
    }

    #[test]
    fn every_row_knows_its_place_in_the_whole_field() {
        // Not its index in the five drawn: the place is out of everybody, so a
        // player sitting tenth reads "10" rather than "5".
        let b = board("a\t900\nb\t800\nc\t700\nd\t600\ne\t500\nf\t400\n");
        let rows = b.standings(1, 5);
        let mine = rows.iter().find(|row| row.is_player).expect("the player is kept");
        assert_eq!(mine.place, 6, "seventh of seven, counting from zero");
    }

    /// A score curve that rises one point per millisecond.
    struct Ramp;

    impl ScoreAt for Ramp {
        fn at(&self, time_ms: f64) -> u64 {
            time_ms.max(0.0) as u64
        }

        fn reached(&self, score: u64) -> f64 {
            score as f64
        }
    }

    #[test]
    fn a_row_that_just_changed_place_is_still_arriving() {
        // The move is worked out from the score curve, not from the frame
        // before — which is what lets frames be drawn in parallel.
        let b = board("a\t300\nb\t200\nc\t100\n");
        // The player passes `c` at 100ms. Just after, the rows that swapped are
        // mid-move; well after, they have settled.
        let moving = b.standings_at(&Ramp, 100.0 + MOVE_MS / 2.0, 5);
        assert!(
            moving.iter().any(|row| row.moving < 1.0 && row.from_place != row.place),
            "somebody should be mid-move: {moving:?}"
        );
        let settled = b.standings_at(&Ramp, 100.0 + MOVE_MS * 2.0, 5);
        assert!(settled.iter().all(|row| row.moving >= 1.0));
        assert!(settled.iter().all(|row| row.from_place == row.place));
    }

    #[test]
    fn nothing_is_moving_before_the_first_pass() {
        let b = board("a\t300\n");
        let rows = b.standings_at(&Ramp, 10.0, 5);
        assert!(rows.iter().all(|row| row.from_place == row.place));
    }
}
