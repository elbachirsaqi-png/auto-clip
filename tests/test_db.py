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


async def test_source_review_flow(db):
    m = await new_moment(db)
    await db.advance(m, Status.SOURCE_PENDING)
    # Un clic « Publier » (étape finale) ne doit pas toucher un clip brut en attente.
    assert await db.set_review_result(m.id, approved=True, stage="final") is False
    assert await db.set_review_result(m.id, approved=True, stage="source") is True
    assert (await db.statuses())[m.id] == Status.SOURCE_APPROVED.value


async def test_category_is_stored(db):
    m = Moment(platform=Platform.TWITCH, channel="x", category="Just Chatting",
               detected_at=datetime.now(UTC), score=3)
    m.id = await db.insert_moment(m)
    claimed = await db.claim(Status.DETECTED)
    assert claimed.category == "Just Chatting"


async def test_old_database_gets_category_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE moments (id INTEGER PRIMARY KEY, platform TEXT NOT NULL, "
                "channel TEXT NOT NULL, detected_at TEXT NOT NULL, score REAL NOT NULL, "
                "status TEXT NOT NULL, clip_url TEXT, video_path TEXT, transcript_path TEXT, "
                "frames_dir TEXT, decision_json TEXT, render_path TEXT, error TEXT, "
                "attempts INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0, "
                "updated_at TEXT NOT NULL)")
    con.commit()
    con.close()
    database = Database(path)
    await database.connect()
    try:
        await new_moment(database)
    finally:
        await database.close()
