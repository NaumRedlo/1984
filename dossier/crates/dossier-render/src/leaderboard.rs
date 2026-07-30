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

    /// The stretch of the standings the play is currently in, worst first.
    ///
    /// **A window around the player, not the top of the map.** On a map forty
    /// people have played, a board that always shows the best four says nothing
    /// about a play sitting thirty-ninth — it is a page from a different story.
    /// The window is the player's own place and the few places immediately above
    /// it, so it starts at the bottom of the field and climbs with them; when
    /// they reach the top the window is the top, and the last row is the leader.
    ///
    /// Returned worst first, so the list is read upwards.
    ///
    /// Ties go to the rival. Two scores level is a moment the player is *about*
    /// to pass somebody, and showing them already ahead reads as a place they
    /// have not earned yet.
    pub fn standings(&self, player_score: u64, limit: usize) -> Vec<Row> {
        let ordered = self.ordered(player_score);
        let mine = ordered.iter().position(|(_, is_player)| *is_player).unwrap_or(0);
        // The player, and up to `limit - 1` better scores above them — but the
        // window is always `limit` long where the field allows it. Near the top
        // there is nothing better left to show, so it fills downward instead:
        // arriving first and being shown alone would be the one moment on the
        // whole board with nothing to compare against.
        let span = limit.max(1).min(ordered.len());
        let mut best = mine.saturating_sub(span - 1);
        let worst = (best + span - 1).min(ordered.len() - 1);
        best = worst.saturating_sub(span - 1);
        ordered[best..=worst]
            .iter()
            .enumerate()
            .map(|(offset, (entry, is_player))| Row {
                entry: entry.clone(),
                is_player: *is_player,
                place: best + offset,
                // Slots count up from the bottom of the window, so the worst of
                // it sits at zero and is drawn first.
                slot: (worst - (best + offset)) as f32,
                from_slot: (worst - (best + offset)) as f32,
                moving: 1.0,
                leaving: false,
            })
            .rev()
            .collect()
    }

    /// The same, with each row told where it is coming from.
    ///
    /// Computed from the score curve rather than remembered between frames — the
    /// player's score at any instant is known in advance, so the instant they
    /// passed each rival is too, and a frame can work out its own animation
    /// without having seen the one before it. That constraint is what lets
    /// frames be drawn in parallel, and it is not negotiable.
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
        let before = self.standings(track.at(last_pass - 1.0), limit);
        let was = |row: &Row| {
            before
                .iter()
                .find(|other| other.is_player == row.is_player && other.entry.name == row.entry.name)
                .map(|other| other.slot)
        };
        for row in &mut rows {
            // A row that was already on the board slides from where it was; one
            // that has just entered rises from below the bottom of the window,
            // which is where it came from.
            row.from_slot = was(row).unwrap_or(-1.0);
            row.moving = progress;
        }
        // Whoever the player displaced is still on their way out, and is drawn
        // going: the place they left has to read as vacated rather than as a row
        // that was never there.
        for old in &before {
            if rows
                .iter()
                .any(|row| row.is_player == old.is_player && row.entry.name == old.entry.name)
            {
                continue;
            }
            rows.push(Row {
                slot: old.slot + 1.0,
                from_slot: old.slot,
                moving: progress,
                leaving: true,
                ..old.clone()
            });
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
}

/// One line of the scoreboard, and its movement.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub entry: Entry,
    pub is_player: bool,
    /// Zero-based place among *everybody*, counting from the best score. What
    /// the row prints — a play sitting thirty-ninth says "39", not "5".
    pub place: usize,
    /// Where the row sits in the drawn window, counting up from the bottom.
    pub slot: f32,
    /// The slot it is arriving from. Equal to `slot` when it is not moving, and
    /// −1 for a row rising into the window from below it.
    pub from_slot: f32,
    /// How far through the move, 0 to 1.
    pub moving: f32,
    /// On its way off the board rather than onto it.
    pub leaving: bool,
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
    fn the_board_is_read_upwards() {
        // A board with the leader on top is a table; one that climbs is a story,
        // and the player's row rising through it is the only thing on screen
        // that changes place.
        let b = board("a\t300\nb\t200\nc\t100\n");
        // Bottom of the field: the player is drawn first, the best last.
        assert_eq!(drawn(&b, 0, 5), ["me", "c", "b", "a"]);
        // Top of it: the player is drawn last, because they are now the best.
        assert_eq!(drawn(&b, 1000, 5), ["c", "b", "a", "me"]);
    }

    #[test]
    fn the_window_follows_the_player_rather_than_the_top_of_the_map() {
        // On a map forty people have played, the best four say nothing about a
        // play sitting thirty-ninth. The window is the player and the few places
        // above them, so it starts at the bottom and climbs with them.
        let field: String = (1..=40).map(|i| format!("p{i}\t{}\n", i * 1000)).collect();
        let b = board(&field);

        // Dead last: the window is the bottom of the field.
        let bottom = b.standings(1, 5);
        assert!(bottom[0].is_player, "the player is drawn first, being worst");
        assert_eq!(bottom[0].place, 40, "last of forty-one");
        assert_eq!(
            bottom.iter().map(|row| row.place).collect::<Vec<_>>(),
            [40, 39, 38, 37, 36],
            "and the four places above them"
        );

        // Halfway up: the window has climbed with them.
        let middle = b.standings(20_500, 5);
        assert_eq!(
            middle.iter().map(|row| row.place).collect::<Vec<_>>(),
            [20, 19, 18, 17, 16]
        );

        // At the top there is nothing better left to show, so the window fills
        // downward instead — arriving first and being shown alone would be the
        // one moment on the whole board with nothing to compare against.
        let top = b.standings(99_999, 5);
        assert_eq!(top.iter().map(|row| row.place).collect::<Vec<_>>(), [4, 3, 2, 1, 0]);
        assert!(top.last().expect("five rows").is_player, "and the player is last");
    }

    #[test]
    fn a_place_is_out_of_everybody_not_out_of_the_five_drawn() {
        let field: String = (1..=40).map(|i| format!("p{i}\t{}\n", i * 1000)).collect();
        let rows = board(&field).standings(1, 5);
        assert_eq!(rows[0].place, 40, "thirty-ninth reads as thirty-nine, not five");
    }

    #[test]
    fn the_player_is_always_on_the_board() {
        // A scoreboard that can hide the play it belongs to is worse than a
        // shorter one.
        let field: String = (1..=40).map(|i| format!("p{i}\t{}\n", i * 1000)).collect();
        let b = board(&field);
        for score in [0, 5_000, 20_000, 39_500, 99_999] {
            assert!(b.standings(score, 5).iter().any(|row| row.is_player), "at {score}");
        }
    }

    #[test]
    fn slots_are_positions_in_the_window_not_places_in_the_field() {
        // They were places once. On a map forty people had played that put the
        // leader three thousand pixels below the frame.
        let field: String = (1..=40).map(|i| format!("p{i}\t{}\n", i * 1000)).collect();
        let rows = board(&field).standings(1, 5);
        let slots: Vec<f32> = rows.iter().map(|row| row.slot).collect();
        assert_eq!(slots, [0.0, 1.0, 2.0, 3.0, 4.0]);
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
        let field: String = (1..=40).map(|i| format!("p{i}\t{}\n", i * 1000)).collect();
        assert_eq!(board(&field).standings(1, 5).len(), 5);
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
        let moving = b.standings_at(&Ramp, 100.0 + MOVE_MS / 2.0, 5);
        assert!(
            moving.iter().any(|row| row.moving < 1.0 && row.from_slot != row.slot),
            "somebody should be mid-move: {moving:?}"
        );
        let settled = b.standings_at(&Ramp, 100.0 + MOVE_MS * 2.0, 5);
        assert!(settled.iter().all(|row| row.moving >= 1.0));
        assert!(settled.iter().all(|row| row.from_slot == row.slot));
    }

    #[test]
    fn the_row_the_player_displaced_is_drawn_on_its_way_out() {
        // The place it left has to read as vacated rather than as a row that was
        // never there.
        // Scores far enough apart that the pass being animated is the one meant:
        // the player crosses `c` at 100ms and the next rival is nowhere near.
        let b = board("a\t9000\nb\t8000\nc\t100\n");
        let rows = b.standings_at(&Ramp, 100.0 + MOVE_MS / 2.0, 2);
        assert!(
            rows.iter().any(|row| row.leaving),
            "somebody should be leaving: {rows:?}"
        );
    }

    #[test]
    fn nothing_is_moving_before_the_first_pass() {
        let b = board("a\t300\n");
        let rows = b.standings_at(&Ramp, 10.0, 5);
        assert!(rows.iter().all(|row| row.from_slot == row.slot));
        assert!(rows.iter().all(|row| !row.leaving));
    }
}
