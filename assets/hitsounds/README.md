# Hit sounds

Sample folders for skins. The engine reads `{set}-hit{sound}.wav` here —
`normal`, `soft` and `drum` crossed with `normal`, `whistle`, `finish` and
`clap`, plus an optional `{set}-slidertick.wav`.

Nothing is shipped here now. The one folder that was, the TickTok samples that
came with the project's own skin, went when that skin did — the engine is
moving to loading the skins players actually use, and a real skin carries its
own sounds.

Any folder works with `--samples <dir>`, and `--kit <name>` selects a
synthesised pack instead. Whatever a folder lacks falls back to synthesis, so a
partial set is fine and no set at all still renders.
