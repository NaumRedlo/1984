//! Finding the `.osu` a replay was played on.
//!
//! A replay names its map only by MD5 — no id, no filename. So the map has to
//! be found by hashing candidates until one matches. `.osz` archives are opened
//! and searched too, since that's how maps arrive from the website.

use std::fs;
use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};

use md5::{Digest, Md5};

/// Where a matching `.osu` came from, for reporting.
#[derive(Debug, Clone)]
pub struct FoundMap {
    pub text: String,
    pub source: String,
}

pub fn md5_hex(bytes: &[u8]) -> String {
    let digest = Md5::digest(bytes);
    let mut out = String::with_capacity(32);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// Read a `.osu` regardless of how it's stored.
///
/// A bare `.osu` is taken at its word — if the caller pointed at a file, they
/// meant it, and refusing a hash mismatch would block the common case of a map
/// edited since the replay was set. An `.osz` has to be searched, so there the
/// hash is the only way in.
pub fn load_map(path: &Path, want_hash: &str) -> Result<FoundMap, String> {
    let bytes = fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let name = path.display().to_string();

    if path
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("osz"))
    {
        return match search_osz(&bytes, want_hash) {
            Ok(Some(found)) => Ok(FoundMap {
                text: found.0,
                source: format!("{name} → {}", found.1),
            }),
            Ok(None) => Err(format!(
                "{name} contains no difficulty with hash {want_hash}"
            )),
            Err(e) => Err(format!("{name}: {e}")),
        };
    }

    Ok(FoundMap {
        text: decode(&bytes),
        source: name,
    })
}

/// Walk a songs directory looking for the map with this hash.
pub fn search_dir(root: &Path, want_hash: &str) -> Result<Option<FoundMap>, String> {
    let mut stack = vec![root.to_path_buf()];
    let mut archives: Vec<PathBuf> = Vec::new();

    // Loose .osu files first: hashing one is a read, while an .osz means
    // inflating every difficulty inside it.
    while let Some(dir) = stack.pop() {
        let entries = match fs::read_dir(&dir) {
            Ok(entries) => entries,
            // An unreadable subdirectory shouldn't abort the whole search.
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            match path.extension().and_then(|e| e.to_str()) {
                Some(ext) if ext.eq_ignore_ascii_case("osu") => {
                    let Ok(bytes) = fs::read(&path) else { continue };
                    if md5_hex(&bytes) == want_hash {
                        return Ok(Some(FoundMap {
                            text: decode(&bytes),
                            source: path.display().to_string(),
                        }));
                    }
                }
                Some(ext) if ext.eq_ignore_ascii_case("osz") => archives.push(path),
                _ => {}
            }
        }
    }

    for archive in archives {
        let Ok(bytes) = fs::read(&archive) else {
            continue;
        };
        if let Ok(Some((text, inner))) = search_osz(&bytes, want_hash) {
            return Ok(Some(FoundMap {
                text,
                source: format!("{} → {inner}", archive.display()),
            }));
        }
    }

    Ok(None)
}

fn search_osz(bytes: &[u8], want_hash: &str) -> Result<Option<(String, String)>, String> {
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|e| format!("not a readable .osz: {e}"))?;

    for i in 0..archive.len() {
        let mut file = archive.by_index(i).map_err(|e| e.to_string())?;
        if !file.name().to_ascii_lowercase().ends_with(".osu") {
            continue;
        }
        let mut contents = Vec::new();
        file.read_to_end(&mut contents).map_err(|e| e.to_string())?;
        if md5_hex(&contents) == want_hash {
            let name = file.name().to_owned();
            return Ok(Some((decode(&contents), name)));
        }
    }
    Ok(None)
}

/// `.osu` files are UTF-8 in practice, sometimes with a BOM. Anything that
/// isn't valid UTF-8 is salvaged rather than rejected — a stray byte in a
/// metadata field shouldn't cost us the whole map.
fn decode(bytes: &[u8]) -> String {
    let body = bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(bytes);
    String::from_utf8_lossy(body).into_owned()
}
