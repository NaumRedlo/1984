//! Little-endian primitive reads over a `.osr` byte buffer.
//!
//! The only unusual type is osu!'s string: a marker byte (`0x00` for "absent",
//! `0x0b` for "present"), then a ULEB128 length, then UTF-8. Every string in the
//! format uses it, so it gets its own reader rather than being open-coded.

use crate::error::{ReplayError, Result};

pub(crate) struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    pub(crate) fn new(data: &'a [u8]) -> Self {
        Self { data, pos: 0 }
    }

    pub(crate) fn remaining(&self) -> usize {
        self.data.len() - self.pos
    }

    fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        if self.remaining() < n {
            return Err(ReplayError::UnexpectedEof {
                offset: self.pos,
                wanted: n,
                left: self.remaining(),
            });
        }
        let slice = &self.data[self.pos..self.pos + n];
        self.pos += n;
        Ok(slice)
    }

    pub(crate) fn u8(&mut self) -> Result<u8> {
        Ok(self.take(1)?[0])
    }

    pub(crate) fn u16(&mut self) -> Result<u16> {
        let b = self.take(2)?;
        Ok(u16::from_le_bytes([b[0], b[1]]))
    }

    pub(crate) fn u32(&mut self) -> Result<u32> {
        let b = self.take(4)?;
        Ok(u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
    }

    pub(crate) fn i32(&mut self) -> Result<i32> {
        Ok(self.u32()? as i32)
    }

    pub(crate) fn u64(&mut self) -> Result<u64> {
        let b = self.take(8)?;
        Ok(u64::from_le_bytes([
            b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
        ]))
    }

    pub(crate) fn i64(&mut self) -> Result<i64> {
        Ok(self.u64()? as i64)
    }

    pub(crate) fn f64(&mut self) -> Result<f64> {
        Ok(f64::from_bits(self.u64()?))
    }

    pub(crate) fn bytes(&mut self, n: usize) -> Result<&'a [u8]> {
        self.take(n)
    }

    /// ULEB128 — the length prefix inside osu!'s string encoding.
    fn uleb128(&mut self) -> Result<usize> {
        let start = self.pos;
        let mut value: u64 = 0;
        let mut shift = 0u32;
        loop {
            let byte = self.u8()?;
            if shift >= 64 {
                return Err(ReplayError::UlebOverflow { offset: start });
            }
            value |= u64::from(byte & 0x7f) << shift;
            if byte & 0x80 == 0 {
                break;
            }
            shift += 7;
        }
        usize::try_from(value).map_err(|_| ReplayError::UlebOverflow { offset: start })
    }

    /// osu! string: `0x00` = absent, `0x0b` = ULEB128 length then UTF-8 bytes.
    ///
    /// An absent string and an empty one are different on the wire but mean the
    /// same thing to every caller here, so both come back as `String::new()`.
    pub(crate) fn string(&mut self) -> Result<String> {
        let start = self.pos;
        match self.u8()? {
            0x00 => Ok(String::new()),
            0x0b => {
                let len = self.uleb128()?;
                let raw = self.take(len)?;
                std::str::from_utf8(raw)
                    .map(str::to_owned)
                    .map_err(|_| ReplayError::BadUtf8 { offset: start })
            }
            marker => Err(ReplayError::BadStringMarker {
                offset: start,
                marker,
            }),
        }
    }
}
