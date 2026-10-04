from autoclip.monitor.detector import SpikeDetector, message_weight


def feed(det: SpikeDetector, values: list[float], channel: str = "c", start: float = 0.0):
    return [det.update(channel, v, start + i * 10) for i, v in enumerate(values)]


def test_no_spike_during_warmup():
    det = SpikeDetector(warmup=5, min_activity=0)
    assert feed(det, [10, 10, 10, 100]) == [None] * 4


def test_spike_detected_after_warmup():
    det = SpikeDetector(warmup=5, ratio=3, min_activity=0)
    results = feed(det, [10] * 10 + [50])
    assert results[-1] is not None and results[-1] >= 3


def test_cooldown_deduplicates():
    det = SpikeDetector(warmup=5, ratio=3, min_activity=0, cooldown_s=60)
    results = feed(det, [10] * 10 + [50, 50, 50])
    assert sum(r is not None for r in results) == 1


def test_min_activity_ignores_quiet_chats():
    det = SpikeDetector(warmup=5, ratio=3, min_activity=20)
    assert feed(det, [1] * 10 + [10])[-1] is None


def test_spike_does_not_raise_baseline():
    det = SpikeDetector(warmup=5, ratio=3, min_activity=0)
    feed(det, [10] * 10)
    before = det.baseline("c")
    feed(det, [100], start=1000)
    assert det.baseline("c") == before


def test_channels_are_independent():
    det = SpikeDetector(warmup=5, ratio=3, min_activity=0)
    feed(det, [100] * 10, channel="big")
    feed(det, [5] * 10, channel="small")
    assert det.update("small", 30, 1000) is not None
    assert det.update("big", 30, 1000) is None


def test_hype_emotes_weigh_more():
    assert message_weight("salut") == 1.0
    assert message_weight("KEKW KEKW") == 2.0
    assert message_weight("KEKW " * 10) == 2.0


def test_kick_emotes_count_as_hype():
    from autoclip.monitor.detector import message_weight
    from autoclip.monitor.kick import normalize_message

    raw = "LOL [emote:37226:KEKW][emote:37226:KEKW]"
    assert normalize_message(raw).split() == ["LOL", "KEKW", "KEKW"]
    assert message_weight(normalize_message(raw)) == 2.0
