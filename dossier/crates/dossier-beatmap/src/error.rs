use thiserror::Error;

#[derive(Debug, Error)]
pub enum BeatmapError {
    #[error("not an osu! beatmap: no \"osu file format\" header on the first non-empty line")]
    MissingHeader,

    #[error("malformed timing point on line {line}: {text:?}")]
    BadTimingPoint { line: usize, text: String },

    #[error("malformed hit object on line {line}: {text:?}")]
    BadHitObject { line: usize, text: String },

    #[error("hit object on line {line} has an unknown type field {type_bits}")]
    UnknownObjectType { line: usize, type_bits: u32 },
}

pub type Result<T> = std::result::Result<T, BeatmapError>;
