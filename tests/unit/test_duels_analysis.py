import pytest

from services.duels import features
from services.duels.osu_file import OsuFileError, parse, slider_path

HEAD = """osu file format v14

[General]
Mode: 0

[Metadata]
Creator:Meg
BeatmapID:658127

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,250,4,2,0,60,1,0
"""

def _map(objects: list[str], head: str = HEAD) -> bytes:
    return (head + "\n[HitObjects]\n" + "\n".join(objects) + "\n").encode()

def _stream(count: int, start: int = 1000, gap: int = 63, x: int = 100) -> list[str]:
    return [f"{x + (4 * i) % 300},192,{start + gap * i},1,0,0:0:0:0:" for i in range(count)]

def test_a_plain_map_is_read_with_its_difficulty_and_hash():
    beatmap = parse(_map(_stream(4)))
    assert (beatmap.format_version, beatmap.cs, beatmap.ar, beatmap.od, beatmap.creator, beatmap.beatmap_id) == (14, 4, 9, 8, "Meg", 658127)
    assert len(beatmap.objects) == 4 and len(beatmap.digest) == 32

@pytest.mark.parametrize("raw, reason", [
    (b"not an osu file", "not_osu_file"),
    (_map(["100,100,1000,1,0", "100,100,900,1,0"]), "objects_out_of_order"),
    (_map(["100,100,-5,1,0"]), "bad_object"),
    (_map(["nan,100,1000,1,0"]), "bad_number:x"),
    (_map([]), "no_objects"),
    (_map(["100,100,1000,2,0,B|200:100,0,-10"]), "bad_slider"),
    (_map(["100,100,1000,1,0"], HEAD.replace("CircleSize:4", "CircleSize:14")), "out_of_range:CircleSize"),
    (_map(["100,100,1000,1,0"], HEAD.replace("0,250,4,2,0,60,1,0", "0,-100,4,2,0,60,0,0")), "no_timing"),
    (b"\xff\xfe broken", "not_utf8"),
])
def test_broken_files_are_refused_with_a_reason(raw, reason):
    with pytest.raises(OsuFileError) as caught:
        parse(raw)
    assert str(caught.value) == reason

def test_a_slider_follows_its_curve_and_stops_at_its_length():
    straight = slider_path("L", [(0, 0), (300, 0)], 100)
    assert straight[-1] == pytest.approx((100, 0))
    arc = slider_path("P", [(0, 0), (50, 50), (100, 0)], 0)
    assert max(point[1] for point in arc) == pytest.approx(50, abs=0.5)
    bent = slider_path("B", [(0, 0), (50, 100), (100, 0)], 0)
    assert bent[len(bent) // 2][1] == pytest.approx(50, abs=1)

def test_slider_duration_uses_the_timing_and_repeats():
    beatmap = parse(_map(["100,100,1000,2,0,L|240:100,2,140"]))
    slider = beatmap.objects[0]
    assert slider.end_time == pytest.approx(1000 + 140 / 140 * 250 * 2)
    assert slider.repeats == 2

def test_streams_are_counted_by_length():
    times = [0, 63, 126, 189, 252, 600, 663, 726, 1500]
    assert features.streams(times) == [5, 3]
    assert features.streams([0, 100, 400, 1000]) == []

def test_double_time_and_hard_rock_change_time_size_and_reading():
    beatmap = parse(_map(_stream(20)))
    plain = features.analyse(beatmap)
    fast = features.analyse(beatmap, "NFDT")
    hard = features.analyse(beatmap, "NFHR")
    assert fast["interval_ms"]["median"] == pytest.approx(plain["interval_ms"]["median"] / 1.5)
    assert fast["effective"]["rate"] == 1.5 and fast["effective"]["ar"] > plain["effective"]["ar"]
    assert hard["effective"]["cs"] == pytest.approx(5.2) and hard["effective"]["radius"] < plain["effective"]["radius"]
    assert plain["streams"]["lengths"]["16-31"] == 1 and plain["streams"]["share"] == 1.0

def test_windows_tell_a_short_burst_from_steady_work():
    lines = (_stream(16, start=20000) + [f"{100 + 50 * i},100,{1000 + 2000 * i},1,0" for i in range(0, 9)]
             + [f"{100 + 50 * i},100,{22000 + 2000 * i},1,0" for i in range(0, 9)])
    burst = parse(_map(sorted(lines, key=lambda line: int(line.split(",")[2]))))
    steady = parse(_map(_stream(300, start=1000, gap=125)))
    loose = features.analyse(burst)["tap_windows"]["2s"]
    even = features.analyse(steady)["tap_windows"]["2s"]
    assert loose["max"] > 3 * loose["median"]
    assert even["loaded_share"] > 0.9 and loose["loaded_share"] < 0.3
    assert set(even) == {"median", "p90", "p99", "max", "loaded_share", "longest_rest"}

def test_the_last_note_does_not_make_a_false_peak_of_load():
    found = features.windows([0.0, 1.0, 2.0], [0.0, 1.0, 1.0], 2.0, 2.0)
    shifted = features.windows([0.0, 1.0, 1.999], [0.0, 1.0, 1.0], 1.999, 2.0)
    assert found["max"] == pytest.approx(1.0) and shifted["max"] == pytest.approx(1.0)
    assert features.FEATURE_VERSION == "features-v2"

def test_reading_counts_objects_on_screen_at_once():
    crowded = features.analyse(parse(_map(_stream(30, gap=40))))
    sparse = features.analyse(parse(_map(_stream(30, gap=900))))
    assert crowded["reading"]["visible"]["median"] > sparse["reading"]["visible"]["median"]
    assert features.preempt(9) == pytest.approx(600) and features.preempt(5) == 1200 and features.preempt(0) == 1800

def _archive(files: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, raw in files.items():
            bundle.writestr(name, raw)
    return buffer.getvalue()

async def test_maps_come_from_mirror_archives_and_never_from_osu_ppy_sh(tmp_path):
    import hashlib

    from services.duels import mirrors

    wanted = _map(_stream(5))
    other = _map(_stream(5)).replace(b"BeatmapID:658127", b"BeatmapID:1")
    asked = []

    async def fetch(address):
        asked.append(address)
        if "osu.direct" in address:
            return b"not a zip"
        return _archive({"a [Other].osu": other, "a [Wanted].osu": wanted, "bg.jpg": b"x"})

    source, raw = await mirrors.fetch_osu(fetch, 77, 658127, None)
    assert (source, raw) == ("catboy.best", wanted)
    assert await mirrors.fetch_osu(fetch, 77, None, hashlib.md5(other).hexdigest()) == ("catboy.best", other)
    assert await mirrors.fetch_osu(fetch, 77, 999, None) is None
    assert all("osu.ppy.sh" not in address for address in asked)
    digest = mirrors.store(str(tmp_path), wanted)
    assert (tmp_path / f"{digest}.osu").read_bytes() == wanted and digest == hashlib.md5(wanted).hexdigest()
    assert mirrors.store(str(tmp_path), wanted) == digest

def _features(kind: str, seed: int):
    import random

    from services.duels import features as made

    rng = random.Random(seed)
    lines = []
    moment = 1000
    for index in range(120):
        if kind == "stream":
            moment += 63 + rng.randint(-2, 2)
            x, y = 200 + (index * 6) % 120, 192 + rng.randint(-4, 4)
        else:
            moment += 250 + rng.randint(-10, 10)
            x, y = (60, 60) if index % 2 else (452 - rng.randint(0, 40), 324 - rng.randint(0, 40))
        lines.append(f"{x},{y},{moment},1,0")
    return made.analyse(parse(_map(lines)))

def _trained():
    from services.duels.slot_model import SlotModel, vector

    rows, labels = [], []
    for seed in range(12):
        rows.append(vector(_features("stream", seed)))
        labels.append("NM2")
        rows.append(vector(_features("jumps", seed)))
        labels.append("NM1")
    return SlotModel.fit(rows, labels, steps=150)

def test_the_slot_model_learns_streams_from_jumps_and_explains_itself():
    model = _trained()
    stream = model.predict(_features("stream", 99), base_ar=9, hr_cs=5.2)
    jumps = model.predict(_features("jumps", 99), base_ar=9, hr_cs=5.2)
    assert (stream.slot, jumps.slot) == ("NM2", "NM1")
    assert stream.probability > 0.8 and stream.margin > 0.5
    assert len(stream.reasons) == 5 and all(isinstance(name, str) for name, _ in stream.reasons)

def test_slot_filters_and_unknown_inputs_keep_maps_out():
    from services.duels.slot_model import CLASSES, SlotModel

    model = _trained()
    with_hd = SlotModel(model.normaliser, [row[:] for row in model.weights], model.bias[:], model.version, model.classes)
    with_hd.bias[CLASSES.index("HD2")] = 50.0
    assert with_hd.predict(_features("jumps", 1), base_ar=9.0, hr_cs=5.2).probabilities["HD2"] == 0.0
    assert with_hd.predict(_features("jumps", 1), base_ar=8.0, hr_cs=5.2).slot == "HD2"
    broken = _features("stream", 1)
    del broken["reading"]
    assert model.predict(broken, base_ar=9, hr_cs=5.2).outside
    assert model.predict(broken, base_ar=9, hr_cs=5.2).slot is None

def test_a_saved_model_predicts_the_same_and_refuses_other_features():
    from services.duels.slot_model import SlotModel

    model = _trained()
    again = SlotModel.load(model.dump())
    sample = _features("stream", 7)
    assert again.predict(sample, 9, 5.2).probabilities == model.predict(sample, 9, 5.2).probabilities
    body = model.dump()
    body["paths"] = body["paths"][:-1]
    with pytest.raises(ValueError):
        SlotModel.load(body)
    older = model.dump()
    older["features"] = "features-v0"
    with pytest.raises(ValueError):
        SlotModel.load(older)
    legacy = model.dump()
    del legacy["features"]
    assert SlotModel.load(legacy).predict(sample, 9, 5.2).probabilities == model.predict(sample, 9, 5.2).probabilities

def test_a_slot_outside_duels_is_learned_and_named_but_never_assigned():
    from services.duels.slot_model import OTHER, SlotModel, vector

    rows, labels = [], []
    for seed in range(12):
        rows.append(vector(_features("stream", seed)))
        labels.append("NM2")
        rows.append(vector(_features("jumps", seed)))
        labels.append("FM1")
    model = SlotModel.fit(rows, labels, steps=150)
    assert "FM1" in model.classes and OTHER not in model.classes
    guess = model.predict(_features("jumps", 99), base_ar=9, hr_cs=5.2)
    assert max(guess.probabilities, key=guess.probabilities.get) == "FM1" and guess.slot is None
    again = SlotModel.load(model.dump())
    assert again.classes == model.classes and again.predict(_features("jumps", 99), 9, 5.2).probabilities == guess.probabilities
    body = model.dump()
    older = model.dump()
    older["classes"] = older["classes"] + [OTHER]
    older["weights"] = older["weights"] + [[0.0] * len(older["weights"][0])]
    older["bias"] = older["bias"] + [-50.0]
    assert SlotModel.load(older).predict(_features("jumps", 99), 9, 5.2).slot is None
    body["classes"] = body["classes"][:-1] + ["XX1"]
    with pytest.raises(ValueError):
        SlotModel.load(body)

def test_the_dt_view_tells_apart_maps_that_look_alike_unmodded():
    import copy
    from services.duels.slot_model import PATHS, SEEN, SlotModel, seen_vector

    rows, labels = [], []
    for seed in range(12):
        plain = _features("jumps", seed)
        slow = copy.deepcopy(plain)
        fast = copy.deepcopy(plain)
        fast["speed_px_ms"]["p90"] = plain["speed_px_ms"]["p90"] * 3
        rows.append(seen_vector(plain, {"NFDT": slow, "NFHR": plain}))
        labels.append("NM1")
        rows.append(seen_vector(plain, {"NFDT": fast, "NFHR": plain}))
        labels.append("DT1")
    model = SlotModel.fit(rows, labels, steps=200, paths=SEEN)
    plain = _features("jumps", 99)
    fast = copy.deepcopy(plain)
    fast["speed_px_ms"]["p90"] = plain["speed_px_ms"]["p90"] * 3
    assert model.predict(plain, 9, 5.2, {"NFDT": fast, "NFHR": plain}).slot == "DT1"
    assert model.predict(plain, 9, 5.2, {"NFDT": plain, "NFHR": plain}).slot == "NM1"
    assert SlotModel.load(model.dump()).paths == SEEN and _trained().paths == PATHS
    assert SlotModel.load(_trained().dump()).predict(plain, 9, 5.2).probabilities

def test_reports_show_every_slot_and_splits_keep_sets_apart():
    from services.duels.slot_model import grouped_split, report

    found = report(["NM1", "NM1", "NM2", "OTHER"], ["NM1", "NM2", "NM2", None])
    assert found["per_slot"]["NM1"] == {"precision": 1.0, "recall": 0.5, "f1": pytest.approx(2 / 3), "support": 2}
    assert found["macro_f1"] == pytest.approx((2 / 3 + 2 / 3) / 2)
    assert found["confusion"]["OTHER"]["NONE"] == 1
    train, test = grouped_split([(1, 2021), (2, 2025), (2, 2022), (3, 2023)], {2025})
    assert (train, test) == ([0, 3], [1])

def test_owc_pools_are_read_with_stage_slot_and_map():
    from services.duels import corpus

    page = """## Qualifiers
| NM1 | [xi - Blue Zenith](https://osu.ppy.sh/beatmapsets/292301#osu/658127) |
| HD1 | [Camellia - Ghost](https://osu.ppy.sh/b/1234) |
### Grand Finals
| NM1 | [xi - Blue Zenith](https://osu.ppy.sh/beatmapsets/292301#osu/658127) |
| TB | [Some - Song](https://osu.ppy.sh/beatmaps/999) |
| DT3 | [Fast - One](https://osu.ppy.sh/beatmapsets/5#osu/6) |
| NM2 | [xi - Blue Zenith](https://osu.ppy.sh/beatmapsets/292301#osu/658127) |
"""
    found = corpus.read_pools(page, 2023, "https://osu.ppy.sh/wiki/en/Tournaments/OWC/2023")
    assert [(e.stage, e.original_slot, e.beatmap_id) for e in found] == [
        ("qualifiers", "NM1", 658127), ("qualifiers", "HD1", 1234), ("grand finals", "NM1", 658127),
        ("grand finals", "TB", 999), ("grand finals", "DT3", 6), ("grand finals", "NM2", 658127)]
    assert found[0].beatmapset_id == 292301 and found[1].beatmapset_id is None
    assert corpus.conflicts(found) == {658127}
    assert len(corpus.distinct(found)) == 5
    assert (corpus.normalised("HD1"), corpus.normalised("NM1"), corpus.normalised("DT3")) == (None, "NM1", None)
