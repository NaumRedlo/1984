from services.render_farm import suggestions


def reply():
    return {
        "beatmapsets": [
            {
                "id": 10,
                "artist": "Nova Tide",
                "title": "Glass Orchard",
                "beatmaps": [
                    {"id": 101, "mode": "osu", "version": "Hard", "difficulty_rating": 3.4, "total_length": 120, "bpm": 170, "checksum": "a" * 32},
                    {"id": 102, "mode": "osu", "version": "Extra", "difficulty_rating": 5.41, "total_length": 121, "bpm": 170, "checksum": "b" * 32},
                    {"id": 103, "mode": "taiko", "version": "Oni", "difficulty_rating": 5.4},
                ],
            },
            "noise",
            {"id": 11, "artist": "Marrow", "title": "Salt", "beatmaps": [{"id": 111, "mode": "osu", "version": "Insane", "difficulty_rating": 5.3}, {"id": "x", "mode": "osu", "difficulty_rating": 5.3}]},
        ]
    }


def test_only_standard_difficulties_inside_the_range_are_offered():
    found = suggestions.picked(reply(), 5.2, 5.5, "NM")
    assert [item["id"] for item in found] == [102, 111]
    assert found[0] == {"id": 102, "set": 10, "artist": "Nova Tide", "title": "Glass Orchard", "version": "Extra", "stars": 5.41, "length": 121, "bpm": 170.0, "md5": "b" * 32}
    assert suggestions.picked(None, 5.2, 5.5, "NM") == []
    assert len(suggestions.picked(reply(), 0, 10, "NM", most=1)) == 1


def test_a_speed_mod_looks_for_easier_maps_and_the_query_says_the_range():
    assert suggestions.query_for(5.0, 5.5, "NM") == "stars>=4.85 stars<=5.65"
    least, most = suggestions.bounds(7.0, 7.0, "DT")
    assert abs(least - 4.85) < 1e-9 and abs(most - 5.15) < 1e-9
    assert [item["id"] for item in suggestions.picked(reply(), 7.4, 7.7, "DT")] == [102, 111]
