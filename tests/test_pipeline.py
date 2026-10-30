"""Uçtan uca akış (ağ ve Gemini taklit edilir; görsel gerçekten üretilir)."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from diploport import article, judge, pipeline, publish, util
from diploport.state import load_state
from conftest import fixture_text

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
HTML = fixture_text("article_daad.html")


def make_item(i, source="Opportunities Corners", **cfg_flags):
    return {"id": f"id{i}", "source": source, "title": f"Raw title {i}", "link": f"https://opportunitiescorners.com/p{i}/",
            "published": datetime.now(timezone.utc).isoformat(), "summary": "",
            "source_cfg": {"assume_eligible": False, "link_fallback_to_source": False, **cfg_flags}}


def result(**kw):
    r = {"is_opportunity": True, "turkey_eligible": "yes",
         "eligibility_evidence": "Applications are open to international students from all nationalities, including candidates from Turkey",
         "unclear_reason_tr": "", "category": "burs", "title_tr": "DAAD Yüksek Lisans Bursu", "organization": "DAAD",
         "country_tr": "Almanya", "deadline": "2026-11-30", "funding_tr": "Tam burs", "fee_required": False,
         "summary_tr": "Almanya'da tam burslu yüksek lisans.", "highlights_tr": ["12-24 ay", "İngilizce", "Aylık 992 €"],
         "apply_link_id": 1, "visual_kind": "scene"}
    r.update(kw)
    return r


@pytest.fixture
def world(cfg, monkeypatch):
    """Ağı, Gemini'yi ve zamanı sabitler. results: sırayla dönecek model çıktıları."""
    w = {"items": [], "results": [], "link_status": {"ok": True, "url": "https://www2.daad.de/deutschland/studienangebote/",
                                                       "status": 200, "soft": False, "reason": ""}, "sent": [], "tg": []}
    monkeypatch.setattr(pipeline, "collect_new", lambda c, s: list(w["items"]))
    monkeypatch.setattr(pipeline, "now_tr", lambda c: NOW)
    monkeypatch.setattr(pipeline, "fetch_article", lambda url, c: article.parse_article(HTML, url, c))
    monkeypatch.setattr(judge, "call_gemini", lambda prompt, c, key=None: w["results"].pop(0))
    monkeypatch.setattr(pipeline, "validate_link", lambda url, c, **k: dict(w["link_status"]))
    return w


def test_eligible_item_flows_to_site_and_outbox(world, cfg):
    world["items"] = [make_item(1)]
    world["results"] = [result()]
    res = pipeline.run(cfg)
    st = load_state()
    assert res["prepared"] == "id1" and st["queue"] == [] and st["seen"]["id1"]["status"] == "queued"
    docs = util.DOCS_DIR
    img = docs / "images" / "id1.jpg"
    assert img.exists() and img.read_bytes()[:3] == b"\xff\xd8\xff"          # gerçek JPEG
    posts = json.loads((docs / "posts.json").read_text())
    assert posts[0]["apply_url"] == "https://www2.daad.de/deutschland/studienangebote/"   # aggregator linki DEĞİL
    thumb = docs / posts[0]["thumb"]                                          # sitedeki kayan kartlar için küçük sürüm
    assert thumb.exists() and thumb.stat().st_size < img.stat().st_size
    from PIL import Image
    with Image.open(thumb) as im:
        assert im.width == 540 and im.height == 675
    assert "opportunitiescorners" not in posts[0]["apply_url"]
    ob = st["outbox"][0]
    assert "Başvuru: https://www2.daad.de" in ob["caption"]
    report = (util.DATA_DIR / "last_run.md").read_text()
    assert "kuyrukta" in report and "all nationalities" in report            # neden/kanıt raporda görünür


def test_send_posts_to_make_then_marks_published(world, cfg, monkeypatch):
    world["items"] = [make_item(1)]; world["results"] = [result()]
    pipeline.run(cfg)
    monkeypatch.setattr(pipeline, "wait_public", lambda url, **k: True)
    monkeypatch.setattr(pipeline, "post_to_make", lambda payload: world["sent"].append(payload))
    assert pipeline.send(cfg) == 1
    p = world["sent"][0]
    assert p["image_url"] == "https://example.github.io/diploport/images/id1.jpg" and p["caption"].startswith("DAAD")
    st = load_state()
    assert st["outbox"] == [] and st["published"][0]["id"] == "id1" and st["seen"]["id1"]["status"] == "published"


def test_send_keeps_item_when_image_not_public_or_make_fails(world, cfg, monkeypatch):
    world["items"] = [make_item(1)]; world["results"] = [result()]
    pipeline.run(cfg)
    monkeypatch.setattr(pipeline, "wait_public", lambda url, **k: False)
    assert pipeline.send(cfg) == 0 and len(load_state()["outbox"]) == 1
    monkeypatch.setattr(pipeline, "wait_public", lambda url, **k: True)
    def fail(payload): raise RuntimeError("down")
    monkeypatch.setattr(pipeline, "post_to_make", fail)
    assert pipeline.send(cfg) == 0 and load_state()["outbox"][0]["attempts"] == 2


def test_no_official_link_means_not_posted(world, cfg):
    """Eski hata: aggregator linkini başvuru linki yapmak. Artık resmî link yoksa ilan hiç paylaşılmaz."""
    world["items"] = [make_item(1)]; world["results"] = [result(apply_link_id=None)]
    res = pipeline.run(cfg)
    assert res["prepared"] is None and load_state()["seen"]["id1"]["status"] == "rejected"
    assert "resmî başvuru linki" in load_state()["seen"]["id1"]["reason"]
    world["items"] = [make_item(2)]; world["results"] = [result()]
    world["link_status"] = {"ok": False, "url": "x", "status": 404, "soft": False, "reason": "HTTP 404"}
    assert pipeline.run(cfg)["prepared"] is None


def test_link_fallback_only_for_trusted_sources(world, cfg):
    world["items"] = [make_item(1, "Youthall", assume_eligible=True, link_fallback_to_source=True)]
    world["results"] = [result(apply_link_id=None, turkey_eligible="unclear", eligibility_evidence="")]
    res = pipeline.run(cfg)
    assert res["prepared"] == "id1"   # Youthall: Türkiye merkezli + kaynak sayfası başvuru sayfası


def test_unclear_without_telegram_is_dropped_with_reason(world, cfg):
    world["items"] = [make_item(1)]
    world["results"] = [result(turkey_eligible="unclear", eligibility_evidence="", unclear_reason_tr="Ülke listesi yok")]
    pipeline.run(cfg)
    s = load_state()["seen"]["id1"]
    assert s["status"] == "rejected" and "Telegram" in (util.DATA_DIR / "last_run.md").read_text()


def test_unclear_with_telegram_waits_for_approval_then_publishes(world, cfg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent_msgs = []
    monkeypatch.setattr(pipeline, "send_pending", lambda it, post: sent_msgs.append(post["title_tr"]) or 77)
    decisions = []
    monkeypatch.setattr(pipeline, "poll_decisions", lambda state: list(decisions))
    cfg["limits"]["publish_window_tr"] = [0, 24]

    world["items"] = [make_item(1)]
    world["results"] = [result(turkey_eligible="unclear", eligibility_evidence="", unclear_reason_tr="Ülke listesi yok")]
    res = pipeline.run(cfg)
    st = load_state()
    assert sent_msgs == ["DAAD Yüksek Lisans Bursu"] and "id1" in st["pending"] and res["prepared"] is None

    world["items"] = []
    decisions.append(("ok", "id1"))
    res = pipeline.run(cfg)
    assert res["prepared"] == "id1" and load_state()["pending"] == {}


def test_telegram_rejection(world, cfg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(pipeline, "send_pending", lambda it, post: 5)
    world["items"] = [make_item(1)]
    world["results"] = [result(turkey_eligible="unclear", eligibility_evidence="")]
    pipeline.run(cfg)
    monkeypatch.setattr(pipeline, "poll_decisions", lambda state: [("no", "id1")])
    world["items"] = []
    pipeline.run(cfg)
    st = load_state()
    assert st["pending"] == {} and st["seen"]["id1"]["reason"] == "elle reddedildi"


def test_rejections_recorded_and_duplicates_skipped(world, cfg):
    world["items"] = [make_item(1), make_item(2), make_item(3)]
    world["results"] = [result(turkey_eligible="no", eligibility_evidence="Only EU citizens"),
                        result(), result(title_tr="DAAD Yüksek Lisans Bursu")]  # 3. ilan aynı resmî link + başlık
    pipeline.run(cfg)
    seen = load_state()["seen"]
    assert seen["id1"]["status"] == "rejected" and seen["id1"]["reason"].startswith("Türkiye'ye kapalı")
    assert seen["id3"]["reason"].startswith("tekrar")


def test_daily_quota_window_and_gap(world, cfg, monkeypatch):
    cfg["limits"]["max_posts_per_day"] = 1
    world["items"] = [make_item(1), make_item(2)]
    world["results"] = [result(), result(title_tr="Başka bir burs", apply_link_id=1)]
    # 2. ilan için farklı resmî link
    calls = {"n": 0}
    def link(url, c, **k):
        calls["n"] += 1
        return {"ok": True, "url": f"https://official{calls['n']}.org/apply", "status": 200, "soft": False, "reason": ""}
    monkeypatch.setattr(pipeline, "validate_link", link)
    res = pipeline.run(cfg)
    st = load_state()
    assert res["prepared"] == "id1" and len(st["queue"]) == 1 and len(st["outbox"]) == 1
    world["items"] = []
    assert pipeline.run(cfg)["prepared"] is None                     # günlük kota doldu
    cfg["limits"]["publish_window_tr"] = [9, 12]
    cfg["limits"]["max_posts_per_day"] = 5
    assert pipeline.run(cfg)["prepared"] is None                     # saat penceresi dışı (14:00)


def test_dry_run_changes_nothing(world, cfg):
    world["items"] = [make_item(1)]; world["results"] = [result()]
    res = pipeline.run(cfg, dry=True)
    assert res["prepared"] is None and not (util.DATA_DIR / "state.json").exists()
    assert "kuyruğa girerdi" in (util.DATA_DIR / "last_run.md").read_text()
    assert json.loads((util.DOCS_DIR / "posts.json").read_text()) == []


def test_gemini_failure_stops_run_without_marking_items(world, cfg, monkeypatch):
    def boom(prompt, c, key=None): raise judge.GeminiError("429")
    monkeypatch.setattr(judge, "call_gemini", boom)
    world["items"] = [make_item(1), make_item(2)]
    pipeline.run(cfg)
    assert "id1" not in load_state()["seen"]    # sonraki çalışmada yeniden denenir


def test_single_gemini_failure_skips_item_and_continues(world, cfg, monkeypatch):
    calls = {"n": 0}

    def flaky(prompt, c, key=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise judge.GeminiError("HTTP 503: high demand")
        return result(title_tr="İkinci ilan")
    monkeypatch.setattr(judge, "call_gemini", flaky)
    world["items"] = [make_item(1), make_item(2)]
    pipeline.run(cfg)
    st = load_state()
    assert "id1" not in st["seen"]                     # geçici hata: işaretlenmedi, sonra yeniden denenecek
    assert st["seen"]["id2"]["status"] == "queued"     # ikinci ilan işlendi, çalışma durmadı
    assert "atlandı" in (util.DATA_DIR / "last_run.md").read_text()


def test_two_consecutive_gemini_failures_stop_the_run(world, cfg, monkeypatch):
    calls = {"n": 0}

    def down(prompt, c, key=None):
        calls["n"] += 1
        raise judge.GeminiError("HTTP 503")
    monkeypatch.setattr(judge, "call_gemini", down)
    world["items"] = [make_item(1), make_item(2), make_item(3)]
    pipeline.run(cfg)
    assert calls["n"] == 2 and load_state()["seen"] == {}


def test_dry_run_report_shows_official_link_deadline_photo_queries_and_sources(world, cfg):
    from diploport import sources
    sources.LAST_STATS.clear(); sources.LAST_STATS["Kaynak A"] = "3 yeni / 10 kayıt"
    world["items"] = [make_item(1)]
    world["results"] = [result(image_queries=["Berlin city", "Germany university", "students campus"])]
    pipeline.run(cfg, dry=True)
    report = (util.DATA_DIR / "last_run.md").read_text()
    assert "link: https://www2.daad.de/deutschland/studienangebote/" in report
    assert "son tarih: 2026-11-30" in report and "foto: Berlin city / Germany university / students campus" in report
    assert "Kaynaklar: Kaynak A: 3 yeni / 10 kayıt" in report and "Bulunan yeni ilan: 1" in report
    sources.LAST_STATS.clear()


def test_expiry(world, cfg):
    st = load_state()
    st["queue"] = [{"id": "x", "title_tr": "Geçmiş", "deadline": "2026-10-01", "queued_at": datetime.now(timezone.utc).isoformat()}]
    from diploport.state import save_state
    save_state(st)
    world["items"] = []
    pipeline.run(cfg)
    s = load_state()
    assert s["queue"] == [] and s["seen"]["x"]["status"] == "expired"


def test_kariyerkapisi_item_uses_api_text_and_own_page_as_apply_link(world, cfg, monkeypatch):
    from diploport import kariyerkapisi
    link = "https://kariyerkapisi.gov.tr/IlanDetay?i=abc-123"
    item = make_item(1, "Kariyer Kapısı", assume_eligible=True, link_fallback_to_source=True, fetcher="kariyerkapisi")
    item["link"] = link
    world["items"] = [item]
    world["results"] = [result(category="is", title_tr="TMSF Fon Uzman Yardımcılığı", apply_link_id=None, country_tr="Türkiye",
                               eligibility_evidence="Türk vatandaşı olmak")]
    monkeypatch.setattr(kariyerkapisi, "fetch_article", lambda it, c: article.Article(
        url=it["link"], final_url=it["link"], title=it["title"], text="Kurum: TMSF\nSon başvuru tarihi: 2026-10-30\nTürk vatandaşı olmak", links=[]))
    monkeypatch.setattr(pipeline, "validate_link", lambda url, c, **k: {"ok": True, "url": url, "status": 200, "soft": False, "reason": ""})
    res = pipeline.run(cfg)
    assert res["prepared"] == "id1"
    posts = json.loads((util.DOCS_DIR / "posts.json").read_text())
    assert posts[0]["apply_url"] == link


def test_kariyerkapisi_degraded_item_is_flagged_in_report(cfg, world, monkeypatch):
    from diploport import kariyerkapisi
    item = make_item(1, "Kariyer Kapısı", assume_eligible=True, link_fallback_to_source=True, fetcher="kariyerkapisi")
    item["link"] = "https://kariyerkapisi.gov.tr/IlanDetay?i=zzz"
    world["items"] = [item]
    world["results"] = [result(category="is", apply_link_id=None, deadline="", eligibility_evidence="ilan Türkiye Cumhuriyeti vatandaşlarına yöneliktir.")]
    monkeypatch.setattr(kariyerkapisi, "fetch_article", lambda it, c: article.Article(
        url=it["link"], final_url=it["link"], title=it["title"], text="İlan başlığı: X\nilan Türkiye Cumhuriyeti vatandaşlarına yöneliktir.", links=[], degraded=True))
    monkeypatch.setattr(pipeline, "validate_link", lambda url, c, **k: {"ok": True, "url": url, "status": 200, "soft": False, "reason": ""})
    pipeline.run(cfg, dry=True)
    assert "özet yalnızca başlıktan" in (util.DATA_DIR / "last_run.md").read_text()


def test_visual_kind_defaults_to_institution_and_source_can_force_it(world, cfg, monkeypatch):
    # model görsel türü vermediyse güvenli varsayılan: kuruma özel (yanlış stok fotoğraf konmaz)
    r = result(); r.pop("visual_kind")
    world["items"] = [make_item(1)]
    world["results"] = [r]
    pipeline.run(cfg, dry=True)
    assert "görsel: kurum logosu aranacak" in (util.DATA_DIR / "last_run.md").read_text()
    # model "scene" dese bile kaynak ayarı (Kariyer Kapısı) kuruma özel + simge zorlar
    seen = {}
    real = pipeline.render_card
    monkeypatch.setattr(pipeline, "render_card", lambda post, c, out, today=None: (seen.update(post=dict(post)), real(post, c, out, today))[1])
    world["items"] = [make_item(2, "Kariyer Kapısı", visual="institution", icon="landmark", link_fallback_to_source=True, assume_eligible=True)]
    world["results"] = [result(visual_kind="scene")]
    pipeline.run(cfg)
    assert seen["post"]["visual_kind"] == "institution" and seen["post"]["icon"] == "landmark"


def test_post_without_logo_waits_and_is_reported_once_while_next_post_is_published(cfg, monkeypatch):
    from diploport import orgvisual, photo
    from diploport.state import load_state
    monkeypatch.setattr(orgvisual, "find_org_visual", lambda *a, **k: None)
    monkeypatch.setattr(orgvisual, "hints_for", lambda *a, **k: {})
    monkeypatch.setattr(photo, "find_background", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(pipeline, "notify", lambda msg: sent.append(msg))

    def post(i, kind, deadline, org=""):
        return {"id": i, "title_tr": f"İlan {i}", "summary_tr": "s", "highlights_tr": [], "category": "is", "country_tr": "Türkiye",
                "funding_tr": "Belirtilmemiş", "visual_kind": kind, "organization": org, "deadline": deadline,
                "apply_url": f"https://example.org/{i}", "source": "Youthall"}

    st = load_state()
    pipeline._enqueue(st, post("a", "institution", "2026-11-01", "Logosuz Kurum"))   # son tarihi yakın: sırada ilk
    pipeline._enqueue(st, post("b", "scene", "2026-12-01"))
    assert pipeline._prepare_next(st, cfg, NOW, NOW.date()) == "b"                    # a atlandı, b yayına hazırlandı
    assert [q["id"] for q in st["queue"]] == ["a"] and st["queue"][0]["visual_alerted"]
    assert len(sent) == 1 and "Logosuz Kurum" in sent[0] and "assets/org_logos/logosuz-kurum.png" in sent[0]
    assert util.DOCS_DIR.joinpath("images", "b.jpg").exists() and not util.DOCS_DIR.joinpath("images", "a.jpg").exists()
    # sonraki çalışma: yalnızca a kaldı, yine logo yok -> hazırlık yok, ikinci kez bildirim YOK
    st["outbox"].clear(); st["published"].clear()
    assert pipeline._prepare_next(st, cfg, NOW, NOW.date()) is None and len(sent) == 1
    assert pipeline.NO_LOGO and "Logosuz Kurum" in pipeline.NO_LOGO[-1]


def test_telegram_pending_message_has_title_summary_link_and_two_buttons(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    def fake_tg(method, **params):
        calls.append((method, params))
        return {"message_id": 9}

    monkeypatch.setattr(publish, "_tg", fake_tg)
    post = {"category": "burs", "title_tr": "DAAD <Bursu>", "summary_tr": "Özet & detay", "apply_url": "https://daad.de/x?a=1&b=2",
            "unclear_reason_tr": "Ülke listesi yok", "country_tr": "Almanya", "deadline": "2026-12-15"}
    assert publish.send_pending({"id": "id1", "source": "AB İlan", "link": "https://ab-ilan.com/x"}, post) == 9
    method, p = calls[0]
    assert method == "sendMessage" and p["parse_mode"] == "HTML"
    assert "DAAD &lt;Bursu&gt;" in p["text"] and "Özet &amp; detay" in p["text"] and "https://daad.de/x?a=1&amp;b=2" in p["text"]
    assert "Ülke listesi yok" in p["text"]
    row = p["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in row] == ["✅ Yayınla", "❌ Yayınlama"] and [b["callback_data"] for b in row] == ["ok:id1", "no:id1"]


def test_pressing_a_button_returns_the_decision_and_shows_it_on_the_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    def fake_tg(method, **params):
        calls.append((method, params))
        if method == "getUpdates":
            return [{"update_id": 5, "callback_query": {"id": "c1", "from": {"id": 42}, "data": "ok:id1", "message": {"message_id": 9}}},
                    {"update_id": 6, "callback_query": {"id": "c2", "from": {"id": 999}, "data": "ok:id2", "message": {"message_id": 10}}},   # başkası: yok sayılır
                    {"update_id": 7, "callback_query": {"id": "c3", "from": {"id": 42}, "data": "no:id3", "message": {"message_id": 11}}}]
        return {}

    monkeypatch.setattr(publish, "_tg", fake_tg)
    state = {"tg_offset": 0}
    assert publish.poll_decisions(state) == [("ok", "id1"), ("no", "id3")] and state["tg_offset"] == 8
    edits = [p for m, p in calls if m == "editMessageReplyMarkup"]
    assert edits[0]["reply_markup"]["inline_keyboard"][0][0]["text"] == "✅ Yayın sırasına alındı"
    assert edits[1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "❌ Yayınlanmayacak"
