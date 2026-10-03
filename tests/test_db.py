from datetime import UTC, datetime

import pytest

from autoclip.db import Database
from autoclip.models import Moment, Platform, Status


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


async def new_moment(db: Database) -> Moment:
    m = Moment(platform=Platform.TWITCH, channel="test", detected_at=datetime.now(UTC), score=4)
    m.id = await db.insert_moment(m)
    return m


async def test_fail_reports_abandon_after_three_attempts(db):
    m = await new_moment(db)
    results = []
    for _ in range(3):
        claimed = await db.claim(Status.DETECTED)
        results.append(await db.fail(claimed, "boom"))
    assert results == [False, False, True]
    assert (await db.statuses())[m.id] == Status.FAILED.value


async def test_review_result_applies_once(db):
    m = await new_moment(db)
    await db.advance(m, Status.PENDING_REVIEW)
    assert await db.set_review_result(m.id, approved=False) is True
    # Deuxième clic (ou clic sur un ancien message) : ignoré.
    assert await db.set_review_result(m.id, approved=True) is False
    assert (await db.statuses())[m.id] == Status.REJECTED.value


async def test_review_ignored_if_not_pending(db):
    m = await new_moment(db)
    assert await db.set_review_result(m.id, approved=True) is False
