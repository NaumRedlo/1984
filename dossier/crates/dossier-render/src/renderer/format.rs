//! Turning numbers and names into the little strings the frame carries.
//!
//! Shared by the HUD and the scoreboard both, which is why it sits under
//! neither: a score is compacted the same way whether it is the player's in the
//! corner or a rival's on a card, and putting the helper inside one of those
//! modules would have the other reach across for it.

/// The widest name a scoreboard card sets at full size before it starts to
/// shrink. osu! caps a name at fifteen characters; this is a real one about
/// that long, used as the yardstick so the rule is measured against something
/// that actually occurs rather than against fifteen of the widest glyph.
const NAME_YARDSTICK: &str = "-legusshhka-";

/// The size to set a name at so it stays inside the yardstick's width.
///
/// Set smaller rather than cut short. A name is somebody's, and `entxrth3vxi…`
/// is not their name — where a shrunk one still is, and osu! caps names at
/// fifteen characters so the worst case is a fifth off the size rather than
/// something unreadable.
///
/// The same treatment the line beneath already gets, so a card that has to
/// give ground gives it the same way twice.
pub(super) fn name_size(name: &str, font: &crate::text::Font, size: f32) -> f32 {
    let room = font.width(NAME_YARDSTICK, size);
    let measured = font.width(name, size);
    if room <= 0.0 || measured <= room {
        return size;
    }
    size * room / measured
}

/// A score in as few characters as it can be said in.
///
/// Three significant figures and a suffix. The board carries totals from two
/// scoring systems three orders of magnitude apart — lazer's standardised
/// million and ScoreV1's hundreds of millions — and the second, grouped in
/// threes, is eleven characters before the accuracy and the mods are appended
/// to it. That line was already being shrunk to fit; this is what it was being
/// shrunk *from*.
///
/// Nothing under ten thousand is touched: a four-figure score is short already,
/// and "9.99k" for 9 994 is longer than the number it replaces.
pub(super) fn compact(value: u64) -> String {
    const STEPS: [(u64, char); 3] = [(1_000_000_000, 'b'), (1_000_000, 'm'), (1_000, 'k')];
    if value < 10_000 {
        return grouped(value);
    }
    for (unit, suffix) in STEPS {
        if value >= unit {
            let scaled = value as f64 / unit as f64;
            // Three significant figures, so the width is the same whichever
            // side of ten or a hundred the number falls on.
            let text = match scaled {
                s if s < 10.0 => format!("{s:.2}"),
                s if s < 100.0 => format!("{s:.1}"),
                s => format!("{s:.0}"),
            };
            return format!("{text}{suffix}");
        }
    }
    grouped(value)
}

/// Digits with a thin space every three from the right: `317 279 960`.
pub(super) fn grouped(value: u64) -> String {
    let digits = value.to_string();
    let mut out = String::with_capacity(digits.len() + digits.len() / 3);
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(' ');
        }
        out.push(c);
    }
    out
}

#[cfg(test)]
mod names {
    use super::{name_size, NAME_YARDSTICK};

    fn font() -> crate::text::Font {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../assets/fonts/TorusNotched-Bold.ttf"
        );
        let bytes = std::fs::read(path).expect("the repo ships this font");
        crate::text::Font::from_bytes(&bytes).expect("and it parses")
    }

    /// The yardstick itself, and anything narrower, is set at full size.
    #[test]
    fn a_name_that_fits_is_left_alone() {
        let font = font();
        assert_eq!(name_size(NAME_YARDSTICK, &font, 20.0), 20.0);
        assert_eq!(name_size("sw1t", &font, 20.0), 20.0);
    }

    /// A longer one is set smaller — never cut. A name is somebody's, and
    /// `entxrth3vxi…` is not their name, where a shrunk one still is.
    #[test]
    fn a_long_name_is_set_smaller_until_it_fits() {
        let font = font();
        let room = font.width(NAME_YARDSTICK, 20.0);
        for name in [
            "WWWWWWWWWWWWWWW",
            "Sakiko Togawa the second",
            "entxrth3vxid_2026",
        ] {
            let size = name_size(name, &font, 20.0);
            assert!(size < 20.0, "{name:?} was not shrunk");
            assert!(
                font.width(name, size) <= room + 1e-3,
                "{name:?} at {size} is still wider than the yardstick"
            );
        }
    }

    /// The whole case for measuring rather than counting characters. Fifteen
    /// `i`s are *narrower* than the twelve-character yardstick and fifteen `W`s
    /// are more than twice as wide; a rule counting characters would shrink a
    /// name that already fitted.
    #[test]
    fn width_is_not_a_count_of_characters() {
        let font = font();
        let narrow = "iiiiiiiiiiiiiii";
        let wide = "WWWWWWWWWWWWWWW";
        assert_eq!(narrow.chars().count(), wide.chars().count());
        assert_eq!(name_size(narrow, &font, 20.0), 20.0);
        assert!(name_size(wide, &font, 20.0) < 20.0);
    }

    /// There is no floor on the shrinking, and there does not need to be one.
    ///
    /// osu! caps a name at fifteen characters, and a real one that long comes
    /// out about a fifth smaller than the rest of the board — measured, 0.79 —
    /// which reads perfectly well. The bound on the pathological case is the
    /// card itself: a name of fifteen `W`s lands at 0.46 and is still inside
    /// its row, which is what the rule is for. A floor would trade that for an
    /// overflow, and an overflow is the thing being fixed.
    #[test]
    fn a_real_long_name_barely_shrinks() {
        let font = font();
        for name in ["Sakiko Togawa t", "entxrth3vxid_20"] {
            let factor = name_size(name, &font, 20.0) / 20.0;
            assert!(
                factor > 0.7,
                "{name:?} came out at {factor:.2} of the size — too small to sit in a list"
            );
        }
    }
}

#[cfg(test)]
mod compacting {
    use super::compact;

    /// The example that prompted it, and the shape either side of it.
    #[test]
    fn a_score_is_said_in_three_figures_and_a_suffix() {
        assert_eq!(compact(1_234_567), "1.23m");
        assert_eq!(compact(12_345_678), "12.3m");
        assert_eq!(compact(125_645_112), "126m");
        assert_eq!(compact(987_654), "988k");
        assert_eq!(compact(87_340), "87.3k");
    }

    /// A four-figure score is short already, and "9.99k" is longer than the
    /// number it would replace.
    #[test]
    fn small_scores_are_left_as_they_are() {
        assert_eq!(compact(0), "0");
        assert_eq!(compact(950), "950");
        assert_eq!(compact(9_994), "9 994");
        assert_eq!(compact(10_000), "10.0k");
    }

    /// Both scoring systems the board carries, side by side: the point is that
    /// they come out the same width despite being three orders apart.
    #[test]
    fn both_scoring_systems_come_out_the_same_width() {
        assert_eq!(compact(1_002_431).len(), compact(125_645_112).len() + 1);
        assert!(compact(125_645_112).len() <= 5);
        assert!(compact(1_002_431).len() <= 5);
    }
}

#[cfg(test)]
mod grouping {
    use super::grouped;

    #[test]
    fn digits_group_in_threes_from_the_right() {
        assert_eq!(grouped(0), "0");
        assert_eq!(grouped(999), "999");
        assert_eq!(grouped(1_000), "1 000");
        assert_eq!(grouped(317_279_960), "317 279 960");
        // The leading group is whatever is left over, not padded to three.
        assert_eq!(grouped(12_345), "12 345");
    }
}
