"""Çekirdek davranışlar: URL temizleme, link ayıklama, kanıt doğrulama, karar kuralları."""
from datetime import date, datetime, timedelta, timezone

import pytest

from diploport import article, judge, sources, util
from diploport.publish import build_caption, build_hashtags
from conftest import fixture_text

TODAY = date(2026, 10, 23)
ITEM = {"id": "abc", "source": "Opportunities Corners", "link": "https://opportunitiescorners.com/daad/",
        "title": "DAAD", "source_cfg": {"assume_eligible": False, "link_fallback_to_source": False}}


# --- util --------------------------------------------------------------------

def test_clean_and_canonical_url():
    u = "https://WWW.Example.com/path/?utm_source=a&id=7&fbclid=zz#frag"
    assert util.clean_url(u) == "https://WWW.Example.com/path/?id=7"
    assert util.canonical_url(u) == "example.com/path?id=7"
    assert util.canonical_url("http://example.com/path") == "example.com/path"


def test_registrable_domain():
    assert util.registrable_domain("www.sub.example.com") == "example.com"
    assert util.registrable_domain("hr.company.com.tr") == "company.com.tr"
    assert util.registrable_domain("www.bbc.co.uk") == "bbc.co.uk"


def test_norm_text_ignores_case_quotes_spacing():
    assert util.norm_text("Open  to ALL “nationalities”, including Türkiye.") == "open to all nationalities including türkiye"


# --- makale ve linkler: "başvuru linki" hatasının çözümü --------------------------

def test_article_links_are_official_only(cfg):
    art = article.parse_article(fixture_text("article_daad.html"), "https://opportunitiescorners.com/daad/", cfg)
    urls = [l["url"] for l in art.links]
    # Aggregator'ın kendi sayfaları, sosyal medya ve paylaşım linkleri aday olamaz
    assert not any("opportunitiescorners.com" in u or "facebook" in u or "twitter" in u or "t.me" in u for u in urls)
    # Resmî link var, izleme parametreleri temizlenmiş ve en üstte
    assert urls[0] == "https://www2.daad.de/deutschland/studienangebote/"
    assert art.links[0]["id"] == 1
    assert "992 euros" in art.text and "Turkey" in art.text


def test_long_article_keeps_head_and_tail(cfg):
    body = "<article><div class='entry-content'><p>HEAD " + "x " * 3000 + "</p><p>Eligibility: Turkey is eligible TAILMARK</p></div></article>"
    art = article.parse_article(body, "https://a.com/p", cfg, max_chars=2000)
    assert art.text.startswith("HEAD") and "TAILMARK" in art.text and len(art.text) < 2100


class FakeResp:
    def __init__(self, status=200, url="https://official.org/apply"):
        self.status_code, self.url = status, url
    def close(self): pass


def test_validate_link_cases(cfg, monkeypatch):
    def fake(resp):
        monkeypatch.setattr(article.requests, "get", lambda *a, **k: resp)
    fake(FakeResp(200, "https://official.org/apply?utm_source=x"))
    v = article.validate_link("https://short.ly/x", cfg)
    assert v["ok"] and v["url"] == "https://official.org/apply" and not v["soft"]
    fake(FakeResp(404)); assert not article.validate_link("https://official.org/x", cfg)["ok"]
    r = (fake(FakeResp(403)), article.validate_link("https://official.org/x", cfg))[1]
    assert r["ok"] and r["soft"]
    # Yönlendirme sonunda aggregator'a düşerse reddedilir
    fake(FakeResp(200, "https://opportunitiescorners.com/post/"))
    v = article.validate_link("https://short.ly/x", cfg)
    assert not v["ok"] and "aggregator" in v["reason"]
    # Ağ hatası
    def boom(*a, **k): raise article.requests.ConnectionError()
    monkeypatch.setattr(article.requests, "get", boom)
    assert not article.validate_link("https://official.org/x", cfg)["ok"]


# --- uygunluk kararı: "gereksiz eleme / yanlış paylaşım" hatalarının çözümü ---------

def base_result(**kw):
    r = {"is_opportunity": True, "turkey_eligible": "yes", "eligibility_evidence":
         "Applications are open to international students from all nationalities, including candidates from Turkey",
         "unclear_reason_tr": "", "category": "burs", "title_tr": "DAAD Bursu", "organization": "DAAD",
         "country_tr": "Almanya", "deadline": "2026-11-30", "funding_tr": "Tam burs", "fee_required": False,
         "summary_tr": "Özet", "highlights_tr": ["a", "b", "c"], "apply_link_id": 1}
    r.update(kw)
    return r


@pytest.fixture
def art(cfg):
    return article.parse_article(fixture_text("article_daad.html"), "https://opportunitiescorners.com/daad/", cfg)


def test_eligible_with_verified_evidence(art):
    v = judge.decide(base_result(), ITEM, art, TODAY)
    assert v["status"] == "eligible" and v["link"]["url"].startswith("https://www2.daad.de")


def test_fabricated_evidence_is_downgraded_to_unclear(art):
    v = judge.decide(base_result(eligibility_evidence="Turkish citizens are guaranteed a full scholarship slot"), ITEM, art, TODAY)
    assert v["status"] == "unclear" and "doğrulanamadı" in v["reason"]


def test_not_eligible_rejected(art):
    v = judge.decide(base_result(turkey_eligible="no", eligibility_evidence="Only for African nationals"), ITEM, art, TODAY)
    assert v["status"] == "reject"


def test_unclear_goes_to_review_not_dropped(art):
    v = judge.decide(base_result(turkey_eligible="unclear", eligibility_evidence="", unclear_reason_tr="Ülke listesi yok"), ITEM, art, TODAY)
    assert v["status"] == "unclear" and v["reason"] == "Ülke listesi yok"


def test_domestic_source_unclear_becomes_eligible(art):
    item = dict(ITEM, source="Youthall", source_cfg={"assume_eligible": True, "link_fallback_to_source": True})
    v = judge.decide(base_result(turkey_eligible="unclear", eligibility_evidence=""), item, art, TODAY)
    assert v["status"] == "eligible" and "Youthall" in v["data"]["eligibility_evidence"]


def test_domestic_source_still_rejects_explicit_no(art):
    item = dict(ITEM, source_cfg={"assume_eligible": True, "link_fallback_to_source": True})
    assert judge.decide(base_result(turkey_eligible="no"), item, art, TODAY)["status"] == "reject"


def test_expired_and_non_opportunity_rejected(art):
    assert judge.decide(base_result(deadline="2026-10-01"), ITEM, art, TODAY)["status"] == "reject"
    assert judge.decide(base_result(is_opportunity=False), ITEM, art, TODAY)["status"] == "reject"


def test_hallucinated_link_id_ignored(art):
    v = judge.decide(base_result(apply_link_id=99), ITEM, art, TODAY)
    assert v["link"] is None
    v = judge.decide(base_result(apply_link_id="1"), ITEM, art, TODAY)
    assert v["link"] is not None


def test_prompt_contains_numbered_links_and_today(art):
    p = judge.build_prompt(ITEM, art, "2026-10-23")
    assert "1 | Apply here (official website) | https://www2.daad.de/" in p and "2026-10-23" in p


# --- RSS toplama ---------------------------------------------------------------

def test_collect_new_filters_old_seen_and_dedupes(cfg, monkeypatch):
    fresh = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    xml = fixture_text("feed.xml").replace("{FRESH}", fresh).encode()

    class R:
        content = xml
        def raise_for_status(self): pass
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    cfg["sources"] = [{"name": "A", "url": "https://a/feed"}, {"name": "B", "url": "https://b/feed"}]
    state = {"seen": {}, "pending": {}, "queue": []}
    items = sources.collect_new(cfg, state)
    # İki kaynak aynı linki verdi -> tekilleştirildi; eski ve linksiz kayıt yok; utm temizlenmiş kimlik
    assert [i["title"] for i in items] == ["Fresh opportunity"]
    state["seen"][items[0]["id"]] = {"ts": "x"}
    assert sources.collect_new(cfg, state) == []


# --- Instagram yazısı -----------------------------------------------------------

def test_caption_has_official_link_and_limits(cfg):
    post = {**base_result(), "id": "abc", "apply_url": "https://www2.daad.de/x", "source": "Opportunities Corners"}
    cap = build_caption(post, cfg)
    assert "Başvuru: https://www2.daad.de/x" in cap and "diploport/#abc" in cap and "30 Kasım 2026" in cap
    assert len(cap) <= 2200 and cap.count("#") <= 8
    long_post = dict(post, summary_tr="x" * 3000)
    assert len(build_caption(long_post, cfg)) <= 2200


def test_hashtags_deduped(cfg):
    assert len(build_hashtags({"category": "burs"}, cfg).split()) == len(set(build_hashtags({"category": "burs"}, cfg).lower().split()))


# --- Kart ve otomatik stok fotoğraf -------------------------------------------

def _photo(pid, w=2400, h=3600, color="#8899aa", photographer="Ada"):
    base = f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg"
    return {"id": pid, "width": w, "height": h, "avg_color": color, "photographer": photographer,
            "url": f"https://www.pexels.com/photo/{pid}/",
            "src": {"original": base, "portrait": base + "?h=1200&w=800", "large2x": base + "?w=940"}}


def test_photo_queries_use_model_then_category_fallback(cfg):
    from diploport import photo
    post = {"category": "burs", "image_queries": ["Zaragoza Spain city", " Spain university ", "", "zaragoza spain city", 5]}
    qs = photo.build_queries(post, cfg)
    assert qs[0] == "Zaragoza Spain city" and qs[1] == "Spain university" and qs[-1] == "university campus students"
    assert len(qs) == len(set(q.lower() for q in qs))
    assert photo.build_queries({"category": "yok"}, cfg)[-1] == cfg["photo"]["fallback_queries"]["diger"]


def test_photo_filters_small_and_dark_and_pick_is_stable(cfg):
    from diploport import photo
    photos = [_photo(1, w=800, h=1200), _photo(2, color="#101010"), _photo(3), _photo(4), _photo(5, color="#ddeeff")]
    good = photo.usable(photos, cfg)
    assert [p["id"] for p in good] == [3, 4, 5]               # küçük ve çok karanlık olanlar elendi, sıra korundu
    assert photo.pick(good, "ilan-a") == photo.pick(good, "ilan-a")
    assert photo.pick([], "x") is None
    assert photo.luma("#ffffff") > 0.99 and photo.luma("#000000") < 0.01 and photo.luma("bozuk") == 0.5


class _Resp:
    def __init__(self, status=200, js=None, content=b"", ctype="application/json"):
        self.status_code, self._js, self.content, self.headers, self.text = status, js, content, {"content-type": ctype}, ""

    def json(self):
        return self._js


def test_find_background_falls_back_to_next_query_and_records_credit(cfg, monkeypatch):
    from diploport import photo
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    calls = []

    def fake_get(url, params=None, headers=None, timeout=0):
        if "api.pexels.com" in url:
            calls.append(params["query"])
            assert headers["Authorization"] == "k" and params["orientation"] == "portrait"
            return _Resp(js={"photos": [] if params["query"] == "nowhere" else [_photo(7, photographer="Ayşe Yılmaz")]})
        assert "w=1080" in url and "h=1350" in url and "fit=crop" in url   # tam kart boyutunda kırpım istenir
        return _Resp(content=b"\xff\xd8" + b"x" * 30000, ctype="image/jpeg")

    monkeypatch.setattr(photo.requests, "get", fake_get)
    bg = photo.find_background({"id": "a", "category": "burs", "image_queries": ["nowhere", "spain city"]}, cfg)
    assert calls == ["nowhere", "spain city"]
    assert bg["credit"] == "Ayşe Yılmaz / Pexels" and bg["query"] == "spain city" and bg["bytes"].startswith(b"\xff\xd8")


def test_find_background_is_none_without_key_or_on_errors(cfg, monkeypatch):
    from diploport import photo
    post = {"id": "a", "category": "burs", "image_queries": ["x"]}
    assert photo.find_background(post, cfg) is None             # anahtar yok
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(photo.requests, "get", lambda *a, **k: _Resp(status=429))
    assert photo.find_background(post, cfg) is None             # kota/hata: hat durmaz, fotoğrafsız devam
    cfg2 = dict(cfg, photo=dict(cfg["photo"], enabled=False))
    assert photo.find_background(post, cfg2) is None


def test_card_subtitle_and_html(cfg):
    from diploport import card
    post = {"title_tr": "Örnek <b>Burs</b>", "category": "burs", "country_tr": "Almanya", "funding_tr": "Tam burs"}
    assert card.subtitle(post) == "Almanya | Tam burs"
    assert card.subtitle(dict(post, funding_tr="Belirtilmemiş")) == "Almanya | Burs"
    assert card.subtitle({"category": "staj", "funding_tr": ""}) == "Staj"
    doc = card.build_html(post, cfg)
    assert "&lt;b&gt;Burs&lt;/b&gt;" in doc and "DİPLOPORT.COM" in doc and "{{" not in doc
    assert "data:image/jpeg" not in doc                             # fotoğraf yoksa şablonun düz hâli
    with_photo = card.build_html(post, cfg, photo=b"\xff\xd8" + b"x" * 50)
    assert "data:image/jpeg;base64," in with_photo


def test_card_renders_1080x1350_jpeg_with_and_without_photo(cfg, tmp_path):
    from diploport import card
    post = {"title_tr": "Almanya'da Erasmus+ Gençlik Çalışanları Eğitimi", "category": "egitim",
            "country_tr": "Almanya", "funding_tr": "Ücretsiz eğitim ve konaklama"}
    plain = card.render_card(post, cfg, tmp_path / "a.jpg")
    with_photo = card.render_card(post, cfg, tmp_path / "b.jpg", photo=plain.read_bytes())
    for f in (plain, with_photo):
        head = f.read_bytes()[:3]
        assert head == b"\xff\xd8\xff" and f.stat().st_size > 20000
    assert "photo_credit" not in post                              # fotoğraf dışarıdan verildi, künye yazılmaz


def test_caption_credits_photographer_only_when_photo_used(cfg):
    post = {"id": "abc", "title_tr": "T", "summary_tr": "S", "highlights_tr": [], "category": "burs",
            "apply_url": "https://x.org/a", "source": "AB İlan", "deadline": ""}
    assert "Görsel:" not in build_caption(post, cfg)
    assert "Görsel: Ayşe Yılmaz / Pexels" in build_caption(dict(post, photo_credit="Ayşe Yılmaz / Pexels"), cfg)


def test_judge_schema_and_prompt_ask_for_image_queries():
    assert "image_queries" in judge.SCHEMA["properties"] and "image_queries" not in judge.SCHEMA["required"]
    assert "image_queries" in judge.PROMPT and "Pexels" in judge.PROMPT
