# Hit sounds

Sample folders for Dossier's skins. The engine reads `{set}-hit{sound}.wav`
here — `normal`, `soft` and `drum` crossed with `normal`, `whistle`, `finish`
and `clap`, plus an optional `{set}-slidertick.wav`.

`1984/` holds the **TickTok** samples, included on their author's free licence
as confirmed by the project owner. Anything the folder lacks — TickTok has no
slider tick, for instance — falls back to the engine's own synthesised kit, so
a partial set is fine and no set at all still renders.

Any other folder works with `--samples <dir>`, and `--kit <name>` selects a
synthesised pack instead. Adding a skin here means adding a `samples_dir()`
entry for it in the CLI, nothing more.
