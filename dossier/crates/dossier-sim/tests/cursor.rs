
/// lazer's own input has two actions and no idea which finger made them, so
/// its replays carry the mouse bits alone — never a keyboard bit, on any
/// frame, in any play. Measured across the corpus: stable writes `M1+K1` for a
/// K1 press and lazer writes `M1`.
mod which_finger {
    use dossier_replay::{Keys, ReplayFrame};
    use dossier_sim::CursorTrack;

    fn track(frames: &[(i64, u8)]) -> CursorTrack {
        CursorTrack::new(
            frames
                .iter()
                .map(|&(time_ms, keys)| ReplayFrame {
                    time_ms,
                    x: 256.0,
                    y: 192.0,
                    keys: Keys(keys),
                })
                .collect(),
        )
    }

    fn held(track: &CursorTrack, lazer: bool) -> [usize; 4] {
        let spans = track.holds_each(lazer);
        [spans[0].len(), spans[1].len(), spans[2].len(), spans[3].len()]
    }

    #[test]
    fn stable_says_which_and_is_taken_at_its_word() {
        // The mouse bit rides along with the keyboard one, so a K1 press must
        // not be counted twice.
        let stable = track(&[(0, 0), (10, Keys::K1 | Keys::M1), (20, 0)]);
        assert_eq!(held(&stable, false), [1, 0, 0, 0]);
    }

    #[test]
    fn a_stable_play_on_the_mouse_alone_still_reads_as_the_mouse() {
        // The rule is about what the file says, not about which client wrote
        // it: somebody who really did play with the mouse says so this way.
        let mouse = track(&[(0, 0), (10, Keys::M1), (20, 0), (30, Keys::M2), (40, 0)]);
        assert_eq!(held(&mouse, false), [0, 0, 1, 1]);
    }

    #[test]
    fn lazer_never_says_and_is_not_read_as_saying_the_mouse() {
        // Every press on the mouse bit and no keyboard bit anywhere. Read by
        // stable's rule this is "they played the whole map with two mouse
        // buttons", which is not unknown — it is false.
        let lazer = track(&[(0, 0), (10, Keys::M1), (20, 0), (30, Keys::M2), (40, 0), (50, Keys::M1), (60, 0)]);
        assert_eq!(held(&lazer, true), [2, 1, 0, 0], "the actions belong in the key lanes");
    }

    #[test]
    fn a_play_with_no_presses_at_all_says_nothing_either_way() {
        let quiet = track(&[(0, 0), (10, 0), (20, 0)]);
        assert_eq!(held(&quiet, false), [0, 0, 0, 0]);
        assert_eq!(held(&quiet, true), [0, 0, 0, 0]);
    }

    #[test]
    fn a_stable_play_that_uses_both_keeps_them_apart() {
        // Which is why this is asked of the client and not of the frames: a
        // stable play with no keyboard bit at all is a real mouse player, and
        // reading the frames alone would move their presses into the keyboard's
        // lanes — the same false statement, the other way round.
        let mixed = track(&[
            (0, 0),
            (10, Keys::M1),
            (20, 0),
            (30, Keys::K1 | Keys::M1),
            (40, 0),
        ]);
        assert_eq!(held(&mixed, false), [1, 0, 1, 0]);
    }
}
