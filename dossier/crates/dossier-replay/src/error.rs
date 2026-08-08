use thiserror::Error;

#[derive(Debug, Error)]
pub enum ReplayError {
    #[error("unexpected end of file: wanted {wanted} byte(s) at offset {offset}, {left} left")]
    UnexpectedEof {
        offset: usize,
        wanted: usize,
        left: usize,
    },

    #[error("bad string marker 0x{marker:02x} at offset {offset} (expected 0x00 or 0x0b)")]
    BadStringMarker { offset: usize, marker: u8 },

    #[error("ULEB128 length at offset {offset} does not fit in usize")]
    UlebOverflow { offset: usize },

    #[error("string at offset {offset} is not valid UTF-8")]
    BadUtf8 { offset: usize },

    #[error("unknown game mode {0}")]
    UnknownMode(u8),

    #[error("replay data is not valid LZMA: {0}")]
    Lzma(String),

    #[error(
        "replay frame data decompresses past the {limit_mb} MB ceiling — \
         a real replay never approaches this, so this is a corrupt or hostile file"
    )]
    DecompressionTooLarge { limit_mb: usize },

    #[error("malformed replay frame {frame:?}")]
    BadFrame { frame: String },
}

pub type Result<T> = std::result::Result<T, ReplayError>;
