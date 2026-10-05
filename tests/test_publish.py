from datetime import UTC, datetime, timedelta

from autoclip import pipeline as pl
from autoclip.config import Settings
from autoclip.models import EditDecision, Moment, Platform, Status
from autoclip.publish import channels, kit
from autoclip.publish.browser import split_caption


def moment(channel="kaicenat", category="Just Chatting", id_=None) -> Moment:
    return Moment(id=id_, platform=Platform.TWITCH, channel=channel, category=category,
                  detected_at=datetime.now(UTC), score=4)


def test_routing_by_streamer_then_category():
    dests = [d.model_copy() for d in channels.DEFAULTS]
    assert channels.route(moment("KaiCenat"), dests).id == "streamers"
    assert channels.route(moment("eslcs", "Counter-Strike"), dests).id == "counter-strike"
    # GTA 6 est prête mais désactivée ; GTA V ne doit jamais tomber dedans.
    assert channels.route(moment("x", "Grand Theft Auto VI"), dests) is None
    dests[2].enabled = True
    assert channels.route(moment("x", "Grand Theft Auto VI"), dests).id == "gta6"
    assert channels.route(moment("x", "Grand Theft Auto V (GTA)"), dests) is None
    assert channels.route(moment("payo", "World of Warcraft"), dests) is None


def test_split_caption():
    text, tags = split_caption("Big moment\n\n🎥 xqc (Kick)\n\n#xqc #gaming")
    assert text == "Big moment\n\n🎥 xqc (Kick)"
    assert tags == ["#xqc", "#gaming"]


async def test_publish_queue_spacing_and_retry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # data/… relatifs : tout se passe dans le dossier du test
    monkeypatch.setattr(pl, "PAUSE_FILE", tmp_path / "paused")
    dest = channels.Destination(id="streamers", name="Streamers", streamers=["kaicenat"],
                                tiktok=False, max_per_day=2, min_gap_minutes=60)
    channels.save([dest])
    dest.profile_dir("youtube").mkdir(parents=True)
    (dest.profile_dir("youtube") / ".connected").write_text("ok")

    p = pl.Pipeline(Settings(_env_file=None, db_path=tmp_path / "t.db", work_dir=tmp_path / "w"))
    await p.db.connect()
    sent, calls = [], []

    async def fake_send(text):
        sent.append(text)

    async def fake_publish(d, platform, video, **kw):
        calls.append((d.id, platform, video.name))
        if len(calls) == 1:
            raise RuntimeError("bouton introuvable")
        return "https://youtube.com/shorts/abc"

    p.review.send_text = fake_send
    monkeypatch.setattr(pl, "publish", fake_publish)
    decision = EditDecision(keep=True, reason="", cuts=[{"start_s": 0, "end_s": 20}], hook="h",
                            creative_direction="", highlight_words=[], title="Kai wins",
                            hashtags=["kai"])
    try:
        ids = []
        for _ in range(2):
            m = moment()
            m.id = await p.db.insert_moment(m)
            await p.db.advance(m, Status.APPROVED, decision_json=decision.model_dump_json())
            video = tmp_path / "final.mp4"
            video.write_bytes(b"x")
            kit.build_kit(m, decision, video)
            ids.append(m.id)

        assert await p.publish_next()  # #1 : échec, nouvel essai dans 30 min
        assert "Échec" in sent[-1] and "1/3" in sent[-1]
        assert await p.publish_next()  # la file n'est pas bloquée : #2 part
        assert sent[-1].startswith("✅ Publié sur YouTube") and "shorts/abc" in sent[-1]
        assert not await p.publish_next()  # espacement de 60 min : rien d'autre maintenant

        # Une heure plus tard et le délai de nouvel essai passé, #1 part à son tour.
        await p.db.conn.execute("UPDATE publications SET published_at = ?",
                                ((datetime.now(UTC) - timedelta(minutes=61)).isoformat(),))
        await p.db.conn.commit()
        p.publish_failures[(ids[0], "youtube:streamers")] = (1, 0.0)
        assert await p.publish_next()
        assert [c[2].split("_")[1] for c in calls] == [f"{ids[0]:06d}", f"{ids[1]:06d}", f"{ids[0]:06d}"]

        assert not await p.publish_next()  # passe de rangement : tout est publié
        statuses = await p.db.statuses()
        assert statuses[ids[0]] == statuses[ids[1]] == Status.PUBLISHED.value
        assert len(list((tmp_path / "data/publie").glob("*.mp4"))) == 2
        assert not list((tmp_path / "data/a_publier").glob("*.mp4"))
    finally:
        await p.db.close()
        await p.http.aclose()
        await p.review.close()
