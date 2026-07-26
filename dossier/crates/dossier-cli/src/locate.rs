//! Finding the `.osu` a replay was played on.
//!
//! A replay names its map only by MD5 — no id, no filename. So the map has to
//! be found by hashing candidates until one matches. `.osz` archives are opened
//! and searched too, since that's how maps arrive from the website.

use std::fs;
use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};

use md5::{Digest, Md5};

/// A `.osu` and where it came from.
///
/// The origin isn't only for reporting: the audio track sits beside the map,
/// inside the same archive or the same folder, and there is no other way to
/// find it — a `.osu` names its audio by filename alone.
#[derive(Debug, Clone)]
pub struct FoundMap {
    pub text: String,
    pub source: String,
    pub origin: Origin,
}

#[derive(Debug, Clone)]
pub enum Origin {
    /// Inside an `.osz`, which is how maps arrive from the website.
    Archive(PathBuf),
    /// A loose `.osu`; siblings live in the same folder.
    Folder(PathBuf),
}

impl Origin {
    fn of_file(path: &Path) -> Self {
        Self::Folder(path.parent().unwrap_or(Path::new(".")).to_path_buf())
    }
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
                origin: Origin::Archive(path.to_path_buf()),
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
        origin: Origin::of_file(path),
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
                            origin: Origin::of_file(&path),
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
                origin: Origin::Archive(archive.clone()),
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

/// Pull the audio track out to somewhere ffmpeg can read it.
///
/// A file already on disk is used where it lies. One inside an `.osz` has to be
/// unpacked first — ffmpeg can't read into a zip, and teaching it to would mean
/// streaming the track ourselves for no gain.
///
/// Returns `None` when the map names no audio or the track isn't there, which
/// is a reason to render silently rather than to stop.
pub fn extract_audio(origin: &Origin, filename: &str, into: &Path) -> Option<PathBuf> {
    if filename.trim().is_empty() {
        return None;
    }
    match origin {
        Origin::Folder(folder) => {
            let path = folder.join(filename);
            path.is_file().then_some(path)
        }
        Origin::Archive(archive) => {
            let bytes = fs::read(archive).ok()?;
            let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).ok()?;
            // Archives are inconsistent about case and about `/` vs `\`, and a
            // map that names `audio.mp3` may well hold `Audio.MP3`.
            let wanted = normalise(filename);
            let index = (0..zip.len()).find(|&i| {
                zip.by_index(i)
                    .map(|f| normalise(f.name()) == wanted)
                    .unwrap_or(false)
            })?;

            let mut file = zip.by_index(index).ok()?;
            let mut contents = Vec::new();
            file.read_to_end(&mut contents).ok()?;

            let extension = Path::new(filename).extension().and_then(|e| e.to_str());
            let out = into.join(format!("audio.{}", extension.unwrap_or("mp3")));
            fs::write(&out, contents).ok()?;
            Some(out)
        }
    }
}

fn normalise(name: &str) -> String {
    name.replace('\\', "/")
        .rsplit('/')
        .next()
        .unwrap_or(name)
        .to_ascii_lowercase()
}
