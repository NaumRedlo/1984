//! Parser tests against replays built byte-by-byte from the format spec.
//!
//! No `.osr` fixture is checked in: constructing the bytes here means a failure
//! points at one field rather than "something in this opaque blob changed", and
//! the builder doubles as executable documentation of the layout.

use std::io::Cursor;

use dossier_replay::{bits, GameMode, Keys, Replay, ReplayError};

// ── building a replay ────────────────────────────────────────────────────

#[derive(Default)]
struct Builder {
    buf: Vec<u8>,
}

impl Builder {
    fn u8(&mut self, v: u8) -> &mut Self {
        self.buf.push(v);
        self
    }
    fn u16(&mut self, v: u16) -> &mut Self {
        self.buf.extend_from_slice(&v.to_le_bytes());
        self
    }
    fn i32(&mut self, v: i32) -> &mut Self {
        self.buf.extend_from_slice(&v.to_le_bytes());
        self
    }
    fn u32(&mut self, v: u32) -> &mut Self {
        self.buf.extend_from_slice(&v.to_le_bytes());
        self
    }
    fn i64(&mut self, v: i64) -> &mut Self {
        self.buf.extend_from_slice(&v.to_le_bytes());
        self
    }
    fn f64(&mut self, v: f64) -> &mut Self {
        self.buf.extend_from_slice(&v.to_bits().to_le_bytes());
        self
    }
    /// osu! string: 0x0b, ULEB128 length, UTF-8.
    fn string(&mut self, s: &str) -> &mut Self {
        if s.is_empty() {
            return self.u8(0x00);
        }
        self.u8(0x0b);
        let mut len = s.len();
        loop {
            let mut byte = (len & 0x7f) as u8;
            len >>= 7;
            if len != 0 {
                byte |= 0x80;
            }
            self.buf.push(byte);
            if len == 0 {
                break;
            }
        }
        self.buf.extend_from_slice(s.as_bytes());
        self
    }
    fn bytes(&mut self, b: &[u8]) -> &mut Self {
        self.buf.extend_from_slice(b);
        self
    }
}

fn lzma(text: &str) -> Vec<u8> {
    let mut out = Vec::new();
    lzma_rs::lzma_compress(&mut Cursor::new(text.as_bytes()), &mut out).unwrap();
    out
}

struct Spec<'a> {
    mode: u8,
    player: &'a str,
    mods: u32,
    frames: &'a str,
    with_online_id: bool,
    target_accuracy: Option<f64>,
}

impl Default for Spec<'_> {
    fn default() -> Self {
        Self {
            mode: 0,
            player: "NaumRedlo",
            mods: 0,
            frames: "",
            with_online_id: true,
            target_accuracy: None,
        }
    }
}

fn build(spec: Spec<'_>) -> Vec<u8> {
    let mut b = Builder::default();
    b.u8(spec.mode)
        .i32(20_260_101)
        .string("d41d8cd98f00b204e9800998ecf8427e")
        .string(spec.player)
        .string("0123456789abcdef0123456789abcdef")
        .u16(400) // 300s
        .u16(20) // 100s
        .u16(3) // 50s
        .u16(90) // gekis
        .u16(12) // katus
        .u16(2) // misses
        .i32(12_345_678)
        .u16(613) // max combo
        .u8(0) // not a perfect combo
        .u32(spec.mods)
        .string("0|1,1000|0.8")
        .i64(638_000_000_000_000_000); // Windows ticks

    let compressed = if spec.frames.is_empty() {
        Vec::new()
    } else {
        lzma(spec.frames)
    };
    b.i32(compressed.len() as i32).bytes(&compressed);

    if spec.with_online_id {
        b.i64(4_242_424_242);
    }
    if let Some(acc) = spec.target_accuracy {
        b.f64(acc);
    }
    b.buf
}

// ── header ───────────────────────────────────────────────────────────────

#[test]
fn reads_the_header_fields() {
    let replay = Replay::parse(&build(Spec::default())).unwrap();

    assert_eq!(replay.mode, GameMode::Standard);
    assert_eq!(replay.game_version, 20_260_101);
    assert_eq!(replay.player, "NaumRedlo");
    assert_eq!(replay.beatmap_hash, "d41d8cd98f00b204e9800998ecf8427e");
    assert_eq!(replay.score, 12_345_678);
    assert_eq!(replay.max_combo, 613);
    assert!(!replay.perfect_combo);
    assert_eq!(replay.online_score_id, 4_242_424_242);
    assert_eq!(replay.life_bar, "0|1,1000|0.8");
}

#[test]
fn handles_non_ascii_player_names() {
    // The length prefix counts BYTES, not characters — a name that disagrees
    // with that assumption would desync every field after it.
    let bytes = build(Spec {
        player: "Сновидец",
        ..Spec::default()
    });
    let replay = Replay::parse(&bytes).unwrap();
    assert_eq!(replay.player, "Сновидец");
    assert_eq!(replay.score, 12_345_678); // the fields after it still line up
}

#[test]
fn computes_hit_counts_and_accuracy() {
    let replay = Replay::parse(&build(Spec::default())).unwrap();

    assert_eq!(replay.hits.count_300, 400);
    assert_eq!(replay.hits.count_miss, 2);
    assert_eq!(replay.hits.total_hits(), 425);
    // (300*400 + 100*20 + 50*3) / (300*425)
    assert!((replay.hits.accuracy_std() - 95.80).abs() < 0.01);
}

#[test]
fn empty_replay_reports_full_accuracy_rather_than_dividing_by_zero() {
    let mut b = Builder::default();
    b.u8(0)
        .i32(1)
        .string("")
        .string("nobody")
        .string("")
        .u16(0)
        .u16(0)
        .u16(0)
        .u16(0)
        .u16(0)
        .u16(0)
        .i32(0)
        .u16(0)
        .u8(0)
        .u32(0)
        .string("")
        .i64(0)
        .i32(0)
        .i64(0);
    let replay = Replay::parse(&b.buf).unwrap();
    assert_eq!(replay.hits.total_hits(), 0);
    assert_eq!(replay.hits.accuracy_std(), 100.0);
}

// ── mods ─────────────────────────────────────────────────────────────────

#[test]
fn decodes_mods() {
    let bytes = build(Spec {
        mods: bits::HIDDEN | bits::DOUBLE_TIME | bits::HARD_ROCK,
        ..Spec::default()
    });
    let replay = Replay::parse(&bytes).unwrap();

    assert!(replay.mods.contains(bits::HIDDEN));
    assert!(!replay.mods.contains(bits::FLASHLIGHT));
    assert_eq!(replay.mods.to_string(), "HDHRDT");
    assert_eq!(replay.mods.speed_multiplier(), 1.5);
}

#[test]
fn nightcore_displays_as_itself_not_as_doubletime() {
    // osu! sets DT's bit alongside NC, so a naive decode prints "DTNC".
    let bytes = build(Spec {
        mods: bits::DOUBLE_TIME | bits::NIGHTCORE,
        ..Spec::default()
    });
    let replay = Replay::parse(&bytes).unwrap();
    assert_eq!(replay.mods.to_string(), "NC");
    assert_eq!(replay.mods.speed_multiplier(), 1.5); // still time-scaled
}

#[test]
fn perfect_hides_the_suddendeath_bit_it_carries() {
    let bytes = build(Spec {
        mods: bits::SUDDEN_DEATH | bits::PERFECT,
        ..Spec::default()
    });
    assert_eq!(Replay::parse(&bytes).unwrap().mods.to_string(), "PF");
}

#[test]
fn no_mods_reads_as_nomod() {
    let replay = Replay::parse(&build(Spec::default())).unwrap();
    assert!(replay.mods.is_empty());
    assert_eq!(replay.mods.to_string(), "NM");
    assert_eq!(replay.mods.speed_multiplier(), 1.0);
}

// ── frames ───────────────────────────────────────────────────────────────

#[test]
fn frame_times_are_accumulated_into_absolute_values() {
    // Stored deltas 10, 5, 20 -> absolute 10, 15, 35.
    let bytes = build(Spec {
        frames: "10|256|192|0,5|260|190|5,20|300|150|15,",
        ..Spec::default()
    });
    let replay = Replay::parse(&bytes).unwrap();

    let times: Vec<i64> = replay.frames.iter().map(|f| f.time_ms).collect();
    assert_eq!(times, vec![10, 15, 35]);
    assert_eq!(replay.frames[1].x, 260.0);
    assert_eq!(replay.frames[1].y, 190.0);
    assert_eq!(replay.duration_ms(), 25);
}

#[test]
fn the_seed_record_is_not_treated_as_a_frame() {
    // Left in place, `-12345` becomes a sample far in the past and drags the
    // whole timeline with it.
    let bytes = build(Spec {
        frames: "10|256|192|0,20|260|190|0,-12345|0|0|987654321,",
        ..Spec::default()
    });
    let replay = Replay::parse(&bytes).unwrap();

    assert_eq!(replay.frames.len(), 2);
    assert!(replay.frames.iter().all(|f| f.time_ms >= 0));
    assert_eq!(replay.rng_seed, Some(987_654_321));
}

#[test]
fn lead_in_frames_before_zero_are_kept() {
    // The client records cursor movement during the lead-in; those negative
    // times are real data, not corruption.
    let bytes = build(Spec {
        frames: "-1500|100|100|0,500|120|110|0,",
        ..Spec::default()
    });
    let replay = Replay::parse(&bytes).unwrap();
    assert_eq!(replay.frames[0].time_ms, -1500);
    assert_eq!(replay.frames[1].time_ms, -1000);
}

#[test]
fn decodes_key_state() {
    let bytes = build(Spec {
        frames: "0|0|0|0,10|0|0|5,10|0|0|10,10|0|0|16,",
        ..Spec::default()
    });
    let frames = Replay::parse(&bytes).unwrap().frames;

    assert!(!frames[0].keys.is_pressed());
    // K1 sets M1 alongside it, which is how the client records a click.
    assert!(frames[1].keys.contains(Keys::K1) && frames[1].keys.contains(Keys::M1));
    assert!(frames[1].keys.is_pressed());
    assert!(frames[2].keys.contains(Keys::K2) && frames[2].keys.contains(Keys::M2));
    // Smoke on its own is not a hit.
    assert!(frames[3].keys.contains(Keys::SMOKE));
    assert!(!frames[3].keys.is_pressed());
}

#[test]
fn a_replay_with_no_frame_block_parses() {
    let replay = Replay::parse(&build(Spec::default())).unwrap();
    assert!(replay.frames.is_empty());
    assert_eq!(replay.rng_seed, None);
    assert_eq!(replay.duration_ms(), 0);
}

// ── optional tails ───────────────────────────────────────────────────────

#[test]
fn old_replays_without_an_online_id_still_parse() {
    let bytes = build(Spec {
        with_online_id: false,
        ..Spec::default()
    });
    let replay = Replay::parse(&bytes).unwrap();
    assert_eq!(replay.online_score_id, 0);
    assert_eq!(replay.player, "NaumRedlo");
}

#[test]
fn target_practice_accuracy_is_read_only_when_that_mod_is_set() {
    let with = build(Spec {
        mods: bits::TARGET,
        target_accuracy: Some(0.9),
        ..Spec::default()
    });
    assert_eq!(
        Replay::parse(&with).unwrap().target_practice_accuracy,
        Some(0.9)
    );

    let without = build(Spec::default());
    assert_eq!(
        Replay::parse(&without).unwrap().target_practice_accuracy,
        None
    );
}

#[test]
fn timestamp_converts_from_windows_ticks() {
    let replay = Replay::parse(&build(Spec::default())).unwrap();
    // 638e15 ticks ≈ 2022-09-24; assert the epoch shift landed in this century.
    let unix = replay.played_at_unix();
    assert!(unix > 1_600_000_000 && unix < 2_000_000_000, "got {unix}");
}

// ── failure modes ────────────────────────────────────────────────────────

#[test]
fn truncated_input_is_an_error_not_a_panic() {
    let full = build(Spec::default());
    for cut in [0, 1, 5, 20, 40] {
        assert!(
            matches!(
                Replay::parse(&full[..cut]),
                Err(ReplayError::UnexpectedEof { .. })
            ),
            "cut at {cut} should report EOF"
        );
    }
}

#[test]
fn unknown_game_mode_is_rejected() {
    let mut bytes = build(Spec::default());
    bytes[0] = 9;
    assert!(matches!(
        Replay::parse(&bytes),
        Err(ReplayError::UnknownMode(9))
    ));
}

#[test]
fn a_bad_string_marker_is_reported_with_its_offset() {
    let mut bytes = build(Spec::default());
    bytes[5] = 0x42; // marker byte of the beatmap hash
    assert!(matches!(
        Replay::parse(&bytes),
        Err(ReplayError::BadStringMarker {
            offset: 5,
            marker: 0x42
        })
    ));
}

#[test]
fn malformed_frames_are_reported() {
    let bytes = build(Spec {
        frames: "10|256|192|0,this-is-not-a-frame,",
        ..Spec::default()
    });
    assert!(matches!(
        Replay::parse(&bytes),
        Err(ReplayError::BadFrame { .. })
    ));
}
