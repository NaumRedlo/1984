# Reading osu!stable itself

> [`stable-fidelity.md`](stable-fidelity.md) opens by saying stable is closed
> source and that what it does has to come from reimplementations. That is still
> true of its *rules* — the judgement, the windows, the note lock. It is not
> true of everything. The client ships its own pictures and sounds in the clear,
> and obfuscation cannot rename what the game looks up by name at runtime. This
> document is what came out of reading b20231023.3 on those two fronts.
>
> The tool is [`tools/stable.py`](../tools/stable.py) and needs nothing
> installed.

## What the client is made of

    osu!.exe           4 MB    a real assembly: 30,317 metadata names
    osu!gameplay.dll  32 MB    fifty names — a resource assembly, no code
    osu!ui.dll        26 MB    the same
    osu!seasonal.dll   8 MB    the same
    osu!auth.dll               no CLR header at all

The three big DLLs look like code and are not. Each is a `ResourcesStore`
shell — a seven-hundred-byte table stream and fifty names, of which the
interesting ones are `<Module>`, `resourceCulture` and `GetTypeFromHandle` —
against tens of megabytes of payload. The payload is a plain `.resources`
container, unencrypted, holding the game's assets under the names the game asks
for them by.

`osu!.exe` is the game, and it is obfuscated. Eazfuscator has renamed most
types and methods to `#=z...` and moved the string literals out of `#US` — 708
bytes, on a four-megabyte assembly — into an encrypted blob. Reading its IL
means reading `#=zlfIhj$7r1tyi` calling `#=zL50oaQClP8SHZOraJw==`, with every
string it compares against unavailable. **Static decompilation of the rules is
not a short road**, and nothing here has gone down it.

`osu!auth.dll` is the anti-cheat. It has no CLR directory the loader can find
and there is no interoperability reason to look at it, so it has been left
alone.

## What obfuscation could not take

A name the game resolves at runtime cannot be renamed, and a `skin.ini` key is
exactly that. Eight and a half thousand of the thirty thousand names survive,
namespaces among them — `osu.GameplayElements`, `osu.Graphics.Skinning`,
`osu.Audio` — which is enough to say what a renamed type is *about* even when
its own name is gone.

`osu.Graphics.Skinning.SkinOsu` is the one that matters here. Its fields are
stable's entire osu!standard skin vocabulary:

    Colours              TriangleColours       CursorCentre
    CursorExpand         CursorRotate          CursorTrailRotate
    SkinAuthor           SkinName              RawName
    SliderBallFlip       SliderBallFrames      SliderStyle
    FontHitCircle        FontHitCircleOverlap  FontScore
    FontScoreOverlap     FontCombo             FontComboOverlap
    OverlayAboveNumber   AllowSliderBallTint   AnimationFramerate
    LayeredHitSounds     ComboSoundBursts      ComboBurstRandom
    SpinnerFadePlayfield SpinnerFrequencyModulate  SpinnerNoBlink
    Version              isLatestVersion

Two of those are worth pointing at. `isLatestVersion` is a cached answer to the
same question [`Ini::version`](../crates/dossier-render/src/imported.rs) asks —
stable keeps a flag for it rather than comparing every time, which says how
often the check is on a hot path. And `LayeredHitSounds` is a *setting*: whether
the plain hit plays underneath a whistle or a clap is a decision the skin makes,
where this engine always layers.

The element file names — `hitcircleoverlay`, `scorebar-bg` — are string
literals and therefore encrypted. They come out of the resource assemblies
instead, which is better: there they arrive with the pictures attached.

## The default skin

`osu!gameplay.dll` holds 456 items: 416 pictures and 36 sounds, everything in
SD and `@2x` pairs. This is what stable draws when a skin supplies nothing, so
it is the fallback every "missing element" question ends at.

### Sizes worth having

Every one of these is a number this engine states somewhere, either to draw at
or to export at.

| element | stable | | element | stable |
|---|---|---|---|---|
| `hitcircle` | 128×128 | | `scorebar-bg` | 695×44 |
| `hitcircleoverlay` | 128×128 | | `scorebar-colour` | 645×10 |
| `approachcircle` | 126×128 | | `scorebar-marker` | 24×24 |
| `reversearrow` | 78×58 | | `hit300` | 103×60 |
| `sliderb0`…`9` | 118×118 | | `hit100` | 97×57 |
| `sliderfollowcircle` | 259×259 | | `hit50` | 71×57 |
| `sliderscorepoint` | 16×17 | | `hit0` | 65×65 |
| `followpoint` | 16×22 | | `score-0` | 33×46 |
| `cursor` | 76×76 | | `score-x` | 35×49 |
| `cursormiddle` | 32×32 | | `score-comma` | 15×54 |
| `cursortrail` | 10×10 | | `default-1` | 25×50 |
| `lighting` | 184×184 | | `spinner-circle` | 666×666 |
| `lightingN` | 170×170 | | `spinner-approachcircle` | 379×384 |
| `section-pass` | 235×182 | | `spinner-rpm` | 280×56 |

`inputoverlay-background` (193×55), `inputoverlay-key` (43×46), `play-skip`
(196×149), the `ranking-*` panel and every `selection-mod-*` icon live in
`osu!ui.dll` rather than in `osu!gameplay.dll`.

### The judgement marks are not one size

103×60, 97×57, 71×57 and 65×65 — the 300 is the widest, the miss is the
squarest and the tallest, and no two share a height. A rule that brings every
mark to one height is therefore *not* what the game does, and the ceiling in
[`verdict_held`](../crates/dossier-render/src/renderer/overlay.rs) is ours
rather than stable's. It is still the right answer for the skins that need it —
the ones whose `hit0` is twice their `hit300` — but the default is the case it
must not break.

### The bar is new style

The default skin ships `scorebar-marker` and **no** `scorebar-ki`,
`scorebar-kidanger` or `scorebar-kidanger2`. In ppy's own terms:

```csharp
var skin = source.FindProvider(s => getTexture(s, "bg") != null);
isNewStyle = getTexture(skin, "marker") != null;
```

So the default is drawn by the new-style rules — the fill offset at
`(7.5, 7.8) × 1.6` rather than `(3, 10) × 1.6`, the marker centred on the
fill's height rather than sitting at its top, its colour taken from the health
and turning additive past half. This engine implements the old style only,
which is right for any skin that ships its own `scorebar-bg` (the provider is
then that skin, and a skin with a `bg` and no `marker` is old style by
definition) and wrong for a skin that ships none — there the provider is the
default, and the default is new.

### The sound kit is complete

    normal-  soft-  drum-   ×  hitnormal hitwhistle hitclap hitfinish
                               sliderslide slidertick sliderwhistle

    combobreak  count  failsound  sectionpass  sectionfail
    spinnerbonus  spinnerspin  nightcore-{kick,hat,clap,finish}

All three banks, all seven voices, no gaps. This settles a question this engine
has had open in a comment for a long time —
[`SamplePack::get`](../crates/dossier-audio/src/samples.rs) says a bank the
skin does not carry "defers to `Normal`, which is the one liberty left: the
game would reach its own default sounds there, and this engine does not have
them". The game reaches *these*. A skin that omits `soft-hitwhistle` does not
go quiet and does not borrow the normal bank's whistle — it gets the default
skin's `soft-hitwhistle`, which is a different sound from either.

They are ppy's files, so they cannot be shipped here. The shape that works is a
folder the engine can be pointed at, produced by whoever runs it from their own
client with `tools/stable.py assets`.

## What this leaves open

The rules are still out of reach: judgement, note lock, the scoring arithmetic,
the slider tick logic. Those live in obfuscated IL behind encrypted strings, and
[`stable-fidelity.md`](stable-fidelity.md)'s method — grade against danser and
lazer, measure against a corpus of real replays — remains the way to settle
them.

What the client answers is the other half: what stable *has*, at what size,
under what name, and which decisions it lets a skin make. That is the half
where a reimplementation can be checked against the thing itself rather than
against another reimplementation.
