import pytest

from autoclip.models import EditDecision


def make(**overrides) -> EditDecision:
    base = dict(
        keep=True,
        reason="ok",
        cuts=[{"start_s": 2, "end_s": 20}],
        hook="Il ne s'y attendait pas",
        creative_direction="Plein écran, gros sous-titres jaunes",
        highlight_words=["non"],
        title="Titre",
        hashtags=["#twitch"],
    )
    return EditDecision.model_validate(base | overrides)


def test_valid_decision():
    make().validate_against(duration_s=30)


def test_cut_beyond_duration_rejected():
    with pytest.raises(ValueError):
        make(cuts=[{"start_s": 0, "end_s": 45}]).validate_against(duration_s=30)


def test_too_short_rejected():
    with pytest.raises(ValueError):
        make(cuts=[{"start_s": 0, "end_s": 3}]).validate_against(duration_s=30)

