import time

import pytest

from autoclip.db import Database
from autoclip.editor import subscription
from autoclip.editor.subscription import UsageLimitReached, run_claude
from tests.test_db import new_moment


class FakeProc:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout, self.returncode = stdout.encode(), returncode

    async def communicate(self, _input):
        return self.stdout, b""


def fake_cli(monkeypatch, stdout: str):
    async def exec_(*_args, **_kwargs):
        return FakeProc(stdout)

    monkeypatch.setattr(subscription, "claude_cli", lambda: "claude")
    monkeypatch.setattr(subscription.asyncio, "create_subprocess_exec", exec_)


async def test_rejected_rate_limit_raises_with_reset_time(monkeypatch):
    reset = int(time.time()) + 3600
    fake_cli(monkeypatch, '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
             f'"resetsAt":{reset},"rateLimitType":"five_hour"}}}}\n'
             '{"type":"result","is_error":true,"result":"Claude usage limit reached"}\n')
    with pytest.raises(UsageLimitReached) as e:
        await run_claude(system="s", content=[], model="m", effort="medium")
    assert e.value.resets_at == reset
    assert "5 heures" in str(e.value)


async def test_limit_message_without_reset_time(monkeypatch):
    fake_cli(monkeypatch, '{"type":"result","is_error":true,"result":"5-hour limit reached"}\n')
    with pytest.raises(UsageLimitReached) as e:
        await run_claude(system="s", content=[], model="m", effort="medium")
    assert e.value.resets_at > time.time()


async def test_success_returns_structured_output(monkeypatch):
    fake_cli(monkeypatch, '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}\n'
             '{"type":"result","is_error":false,"structured_output":{"keep":true}}\n')
    result, rate = await run_claude(system="s", content=[], model="m", effort="xhigh")
    assert result["structured_output"] == {"keep": True}
    assert rate["status"] == "allowed"


async def test_pause_keeps_status_and_attempts(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    try:
        m = await new_moment(db)
        for _ in range(5):  # bien plus que 3 essais : une pause ne doit jamais faire échouer
            claimed = await db.claim(m.status)
            await db.pause(claimed, "En pause")
        assert (await db.statuses())[m.id] == "detected"
        assert (await db.claim(m.status)).id == m.id
    finally:
        await db.close()
