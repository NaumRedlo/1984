# Hit sounds

Sample folders for Dossier's skins. The engine reads `{set}-hit{sound}.wav`
here — `normal`, `soft` and `drum` crossed with `normal`, `whistle`, `finish`
and `clap`, plus an optional `{set}-slidertick.wav`.

**The `.wav` files themselves are not in the repository, and shouldn't be.**
They come from community skins whose authors haven't licensed them for
redistribution, so committing them would be republishing someone else's work.
The folders are in `.gitignore`; this file is what's tracked.

To give the `1984` skin its sounds, drop a skin's samples into
`assets/hitsounds/1984/`. Anything missing falls back to the engine's own
synthesised kit, so a partial set is fine — and no set at all still renders,
just with the synthesised sounds.

Any folder works with `--samples <dir>` regardless of what's here.
