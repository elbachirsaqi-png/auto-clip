from autoclip.models import Cut
from autoclip.render.hyperframes import build_segments, remap_words

CUTS = [Cut(start_s=2, end_s=5), Cut(start_s=10, end_s=12)]


def test_segments_are_placed_back_to_back():
    segs = build_segments(CUTS)
    assert [(s["out_start"], s["media_start"], s["duration"]) for s in segs] == [
        (0, 2, 3),
        (3, 10, 2),
    ]


def test_words_are_remapped_and_dropped_outside_cuts():
    words = [
        {"w": "avant", "start": 0.5, "end": 1.0},
        {"w": "un", "start": 2.5, "end": 3.0},
        {"w": "coupé", "start": 7.0, "end": 7.5},
        {"w": "deux", "start": 10.5, "end": 11.0},
    ]
    assert remap_words(words, CUTS) == [
        {"w": "un", "start": 0.5, "end": 1.0},
        {"w": "deux", "start": 3.5, "end": 4.0},
    ]
