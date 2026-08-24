//! Stamp the binary with the source it was built from.
//!
//! `version = "0.1.0"` in the manifest has never been bumped and never will be
//! by hand, so it says nothing about which build a machine is running. This
//! does, and it is the only identity two machines can compare: a hash of the
//! binary itself would differ between a Linux build and a macOS one of the
//! same source, which is exactly the pair that needs comparing.
//!
//! Why anyone needs to compare them: a render farm worker runs its own checkout
//! and its own `cargo build`. A worker whose binary is behind the bot's renders
//! with old code and says nothing, and the output looks plausible — which is
//! the same shape of bug as a skin folder unpacked by an importer that has
//! since been fixed, and that one cost a long evening before it was found.
//!
//! ## Why this is not the commit
//!
//! It was, and the farm stopped over it. `drejk-starsij.local` was refused with
//! *"the bot renders with 8aae009 and this worker with 6054b39"*, and the whole
//! difference between those two commits is 241 lines of one markdown file. The
//! two binaries were the same program. A commit identifies the repository, and
//! the repository holds documents, corpus notes and tooling that no render has
//! ever depended on, so every commit to any of them halted the farm and sent
//! the work back to the bot — the failure being invisible, since the fallback
//! renders correctly and only slower.
//!
//! So the stamp names the *inputs to the binary* instead: the `crates` tree,
//! `Cargo.lock` for the versions of everything it links, and the workspace
//! `Cargo.toml` for the profile it was compiled under. Nothing else reaches the
//! binary — there is not one `include_bytes!` in the workspace pointing outside
//! `crates`. Two checkouts agreeing on those three produce the same program
//! whatever else differs between them.
//!
//! Git does the hashing at every step: three object ids for the three inputs,
//! then one object id over the list of them. Folding cryptographic hashes with
//! something hand-rolled would be the one part of this not backed by git, for
//! no gain.
//!
//! Absent git — a release tarball, a Docker build without the `.git` directory
//! — this yields `unknown`, and the check that reads it treats two `unknown`s
//! as "cannot tell" rather than as a match. Guessing agreement is the one
//! answer worse than admitting ignorance.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// The three things a render's output can depend on, relative to the workspace.
///
/// Deliberately not `docs`, not `tools`, not the corpus manifest: a stamp that
/// moves when a document does is a stamp that stops the farm for nothing.
const INPUTS: [&str; 3] = ["crates", "Cargo.lock", "Cargo.toml"];

fn main() {
    let root = workspace_root();

    // Rerun when any input is edited — the `+` has to appear the moment the
    // tree stops matching what it names — and when HEAD moves, since a commit
    // or a checkout changes the ids without touching a file.
    for input in INPUTS {
        println!("cargo:rerun-if-changed={}", root.join(input).display());
    }
    println!("cargo:rerun-if-changed={}", root.join(".git/HEAD").display());
    println!("cargo:rerun-if-changed={}", root.join("../.git/HEAD").display());

    let id = describe(&root).unwrap_or_else(|| "unknown".to_owned());
    println!("cargo:rustc-env=DOSSIER_BUILD={id}");
}

/// `crates/dossier-cli` is two levels below the workspace root.
///
/// Every path here is resolved from there rather than from git's idea of the
/// repository root, because the workspace is not always at it — here it sits in
/// a `dossier/` subdirectory of the bot's repository.
fn workspace_root() -> PathBuf {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default());
    manifest
        .parent()
        .and_then(Path::parent)
        .map_or(manifest.clone(), Path::to_path_buf)
}

/// The short id of the source, with `+` when the tree it was built from had
/// edits in it.
///
/// The `+` matters more than the id on a developer's machine, where the
/// interesting question is not "which source" but "is this build something
/// anybody else could reproduce".
fn describe(root: &Path) -> Option<String> {
    let mut args = vec!["rev-parse".to_owned()];
    args.extend(INPUTS.iter().map(|input| format!("HEAD:./{input}")));
    let ids = git(root, &args, None)?;

    // One id over the three, so the stamp is a single word however many inputs
    // it comes to cover.
    let folded = git(root, &["hash-object".to_owned(), "--stdin".to_owned()], Some(&ids))?;
    let short: String = folded.chars().take(7).collect();
    if short.len() < 7 {
        return None;
    }

    // Only the inputs. An edited document leaves this build reproducible.
    let mut status = vec![
        "status".to_owned(),
        "--porcelain".to_owned(),
        "--untracked-files=no".to_owned(),
        "--".to_owned(),
    ];
    status.extend(INPUTS.iter().map(|input| (*input).to_owned()));
    let dirty = git(root, &status, None).is_some_and(|out| !out.is_empty());

    Some(if dirty { format!("{short}+") } else { short })
}

fn git(root: &Path, args: &[String], stdin: Option<&str>) -> Option<String> {
    let mut child = Command::new("git")
        .args(args)
        .current_dir(root)
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    if let Some(text) = stdin {
        child.stdin.take()?.write_all(text.as_bytes()).ok()?;
    }
    let out = child.wait_with_output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8(out.stdout).ok()?.trim().to_owned())
}
