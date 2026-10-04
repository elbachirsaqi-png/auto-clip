from autoclip import pipeline as pl
from autoclip.config import Settings
from autoclip.review.telegram import ReviewBot


async def make_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "PAUSE_FILE", tmp_path / "paused")
    p = pl.Pipeline(Settings(_env_file=None, db_path=tmp_path / "t.db", work_dir=tmp_path / "work"))
    await p.db.connect()
    return p


async def test_pause_and_resume(tmp_path, monkeypatch):
    p = await make_pipeline(tmp_path, monkeypatch)
    try:
        assert not p.paused
        assert "Pause" in await p.on_command("pause")
        assert p.paused and (tmp_path / "paused").exists()  # survit à un redémarrage
        assert "Déjà en pause" in await p.on_command("pause")
        assert "reparti" in await p.on_command("reprendre")
        assert not p.paused
    finally:
        await p.db.close()
        await p.http.aclose()


async def test_status_and_help(tmp_path, monkeypatch):
    p = await make_pipeline(tmp_path, monkeypatch)
    try:
        status = await p.on_command("statut")
        assert status.startswith("▶ En marche") and "À valider sur Telegram : 0" in status
        assert "/redemarrer" in await p.on_command("aide")
    finally:
        await p.db.close()
        await p.http.aclose()


async def test_only_validation_chat_can_send_commands():
    received, sent = [], []

    async def on_command(cmd):
        received.append(cmd)
        return "ok"

    bot = ReviewBot(Settings(_env_file=None, telegram_bot_token="t", telegram_chat_id="42"),
                    on_decision=None, on_command=on_command)

    async def fake_send(text):
        sent.append(text)

    bot.send_text = fake_send
    await bot._handle_message({"chat": {"id": 999}, "text": "/pause"})  # autre chat : ignoré
    await bot._handle_message({"chat": {"id": 42}, "text": "bonjour"})  # pas une commande
    await bot._handle_message({"chat": {"id": 42}, "text": "/pause@SQClip_bot"})
    await bot._handle_message({"chat": {"id": 42}, "text": "/inconnue"})
    assert received == ["pause", "aide"]
    assert sent == ["ok", "ok"]
    await bot.close()
