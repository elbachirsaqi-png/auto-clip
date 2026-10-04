from datetime import UTC, datetime

from autoclip.models import EditDecision, Moment, Platform
from autoclip.publish import kit


def decision(**overrides) -> EditDecision:
    base = dict(keep=True, reason="ok", cuts=[{"start_s": 0, "end_s": 20}], hook="He did not see it",
                creative_direction="x", highlight_words=[], title="xQc loses it at a jump scare",
                description="xQc gets jump scared in Phasmophobia.", hashtags=["xqc", "#gaming"])
    return EditDecision.model_validate(base | overrides)


MOMENT = Moment(id=61, platform=Platform.KICK, channel="xqc", category="Phasmophobia",
                detected_at=datetime.now(UTC), score=4)


def test_lint_flags_long_vague_title():
    r = kit.lint_title("The most INSANE CRAZY EPIC moment you will ever see on a stream ever", "x")
    assert r["score"] < 60
    assert any("vagues" in i for i in r["issues"])


def test_description_has_credit_and_shorts_tag():
    text = kit.description(decision(), MOMENT)
    assert text.startswith("xQc gets jump scared")
    assert "https://kick.com/xqc" in text
    assert text.rstrip().endswith("#xqc #gaming #Shorts")


def test_build_kit_moves_video_and_writes_text(tmp_path, monkeypatch):
    monkeypatch.setattr(kit, "PUBLISH_DIR", tmp_path / "a_publier")
    video = tmp_path / "final.mp4"
    video.write_bytes(b"mp4")
    mp4, txt = kit.build_kit(MOMENT, decision(title='Bad: "title"?'), video)
    assert not video.exists() and mp4.read_bytes() == b"mp4"
    assert "_000061_xqc_Bad title" in mp4.name
    content = txt.read_text(encoding="utf-8")
    assert content.startswith('TITRE\nBad: "title"?') and "DESCRIPTION" in content
