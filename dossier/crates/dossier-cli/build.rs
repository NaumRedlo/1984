//! Stamp the binary with the commit it was built from.
//!
//! `version = "0.1.0"` in the manifest has never been bumped and never will be
//! by hand, so it says nothing about which build a machine is running. The
//! commit does, and it is the only identity two machines can compare: a hash of
//! the binary itself would differ between a Linux build and a macOS one of the
//! same source, which is exactly the pair that needs comparing.
//!
//! Why anyone needs to compare them: a render farm worker runs its own checkout
//! and its own `cargo build`. A worker whose binary is behind the bot's renders
//! with old code and says nothing, and the output looks plausible — which is
//! the same shape of bug as a skin folder unpacked by an importer that has
//! since been fixed, and that one cost a long evening before it was found.
//!
//! Absent git — a release tarball, a Docker build without the `.git` directory
//! — this yields `unknown`, and the check that reads it treats two `unknown`s
//! as "cannot tell" rather than as a match. Guessing agreement is the one
//! answer worse than admitting ignorance.

use std::process::Command;

fn main() {
    // Rerun when HEAD moves. `.git/HEAD` covers a commit or a checkout; the
    // ref it points at covers a commit on the branch already checked out.
    println!("cargo:rerun-if-changed=../../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../.git/HEAD");

    let commit = describe().unwrap_or_else(|| "unknown".to_owned());
    println!("cargo:rustc-env=DOSSIER_COMMIT={commit}");
}

/// The short hash, with `+` when the tree it was built from had edits in it.
///
/// The `+` matters more than the hash on a developer's machine, where the
/// interesting question is not "which commit" but "is this build something
/// anybody else could reproduce".
fn describe() -> Option<String> {
    let head = run(&["rev-parse", "--short=7", "HEAD"])?;
    let dirty = run(&["status", "--porcelain", "--untracked-files=no"])
        .is_some_and(|out| !out.is_empty());
    Some(if dirty { format!("{head}+") } else { head })
}

fn run(args: &[&str]) -> Option<String> {
    let out = Command::new("git").args(args).output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8(out.stdout).ok()?.trim().to_owned())
}
