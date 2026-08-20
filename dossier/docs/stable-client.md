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
where this engine used to layer unconditionally. It reads the key now, which is
also why osu! attaches that hit as a *layered* sample rather than an ordinary
one — being layered is what makes it suppressible.

Twelve of the twenty-nine are still unread here. `OverlayAboveNumber`,
`CursorCentre`, `CursorRotate`, `CursorTrailRotate`, `SliderBallFlip`,
`SliderStyle`, `SpinnerFadePlayfield`, `SpinnerNoBlink`,
`SpinnerFrequencyModulate`, `ComboSoundBursts` and `ComboBurstRandom` each
change something a viewer would see or hear.

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

They are ppy's files, so they cannot be shipped here — the folder is deployment
state, produced by whoever runs the engine from their own client:

    tools/stable.py assets ~/osu!/osu!gameplay.dll ~/.dossier/osu-sounds
    dossier video --game-sounds ~/.dossier/osu-sounds ...    # or $DOSSIER_GAME_SOUNDS

All twenty-one banked voices come out as plain WAVs and need nothing
converting; `combobreak`, `sectionpass`, `sectionfail` and `failsound` are MP3
in the client and this engine reads WAV, so those four stay the skin's.

The lookup is now the game's, step for step — beatmap, skin, osu! — with the
old normal-bank liberty left in place *below* the new step, where it only fires
for a host that has supplied no folder. A blank still ends the search wherever
it is found: laying osu!'s own underneath must not put back a sound somebody
deliberately removed.

## How the client actually reads a skin

The types survived. All nine in `osu.Graphics.Skinning` keep their own names —
`Skin`, `SkinOsu`, `SkinFruits`, `SkinMania`, `Section`, `SliderStyle`,
`ComboBurstStyle`, `ManiaNoteBodyStyle`, `ManiaSpecialStyle` — and so do their
fields. Only the *methods* were renamed, and a renamed method still says what it
does by what it calls: a call into mscorlib keeps its real name however hard the
assembly around it has been obfuscated. `stable.py il` prints exactly that.

### Parsing

`Skin`'s largest method is the `skin.ini` reader, and it reads like one:

    File.OpenRead → new StreamReader → TextReader.ReadLine
      String.StartsWith          the `[` of a section header
      String.IndexOf / Substring / Trim
      String.op_Equality         against a decrypted section name
      new SkinOsu()              …or new SkinMania(), or the fruits skin
      Int32.TryParse             the mania key count
      ManiaSkins.Add

Line by line, no lookahead, one section object at a time. `Skin` also carries
`defaultSkin`, `defaultFields` and `defaultProperties` — a default instance and
its reflected members, which is how an absent key gets an answer.

### Every key is an explicit lookup

`SkinOsu` has one long method that is nothing but this pattern repeated:

    ldc.i4    <string id>
    call      Skin::«get»<T>          a generic getter, one per type
    stfld     CursorExpand

So the keys are string literals rather than reflected field names, and those
literals are encrypted. What is *not* encrypted is which field each lands in and
what type it is read as, because both are in the metadata.

### The defaults, which are the useful part

They are not read from the file at all — they are the field initialisers in
`SkinOsu`'s constructor, and those are plain IL:

| field | stable | this engine |
|---|---|---|
| `FontHitCircleOverlap` | −2 | −2 |
| `FontScoreOverlap` | not set → 0 | 0 |
| `FontComboOverlap` | not set → 0 | 0 |
| `FontHitCircle` | `"default"` | `"default"` |
| `FontScore` | `"score"` | `"score"` |
| `FontCombo` | **the same string id as `FontScore`** | `"score"` |
| `Version` | 1 | 1 when a file exists |
| `LayeredHitSounds` | 1 | true |
| `AnimationFramerate` | −1 | −1 |
| `CursorExpand` | 1 | true |
| `AllowSliderBallTint` | not set → false | false |
| `OverlayAboveNumber` | 1 | **was under; now over** |
| `CursorCentre` | 1 | not read |
| `CursorRotate` | 1 | not read |
| `SpinnerFrequencyModulate` | 1 | not read |
| `SpinnerFadePlayfield` | 0 | not read |
| `SliderBallFrames` | 10 | not read |
| `SliderStyle` | 2 | not read |
| `SkinAuthor` | `""` | not read |

Eleven of the twelve keys this engine already had came out exactly right, which
is as good a check on a reimplementation as there is. `FontCombo` is the
strongest of them: it is not merely *a* default of `"score"` but **literally the
same string id** as `FontScore`, so the combo counter falling back to the score
font rather than to a `combo-` one is settled rather than inferred.

The twelfth was wrong. `OverlayAboveNumber` defaults to 1 and this engine drew
the figure last, putting the rim behind it. On a skin whose `hitcircleoverlay`
is a thin ring that is a hairline; on a skin whose overlay is the face of the
note it is the whole note in the wrong order.

### What is out of reach

The string literals are properly encrypted. The decryptor is one method — every
comparison in the parser is preceded by a call to it — and it builds its
resource name a character at a time, seeks into an embedded blob and reads
through a stream cipher whose key comes from runtime state, behind `StackTrace`
checks on its own caller. The two blobs measure 7.87 and 7.95 bits of entropy
per byte with no periodicity, so there is nothing to recover statistically.
Reading them would mean reimplementing Eazfuscator, not reading osu!.

That costs less than it sounds. The keys are documented on the wiki and shipped
in every skin in the wild; what the binary was wanted for was the defaults and
the shape, and both are in the clear.

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
