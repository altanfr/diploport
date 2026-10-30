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
    assert sources.LAST_STATS["A"].startswith("1 yeni") and "B" in sources.LAST_STATS   # rapor için kaynak özeti
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

def _pexels_raw(pid, w=2400, h=3600, color="#8899aa", photographer="Ada"):
    base = f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg"
    return {"id": pid, "width": w, "height": h, "avg_color": color, "photographer": photographer,
            "url": f"https://www.pexels.com/photo/{pid}/",
            "src": {"original": base, "portrait": base + "?h=1200&w=800", "large2x": base + "?w=940"}}


def _photo(pid, **kw):
    from diploport import photo
    return photo.normalize_pexels(_pexels_raw(pid, **kw))


def _unsplash_raw(pid, w=4000, h=6000, color="#8899aa", name="Can Demir"):
    return {"id": pid, "width": w, "height": h, "color": color, "user": {"name": name},
            "urls": {"raw": f"https://images.unsplash.com/photo-{pid}?ixid=abc", "regular": f"https://images.unsplash.com/photo-{pid}?w=1080"},
            "links": {"html": f"https://unsplash.com/photos/{pid}", "download_location": f"https://api.unsplash.com/photos/{pid}/download?ixid=abc"}}


def test_photo_queries_use_model_then_category_fallback(cfg):
    from diploport import photo
    post = {"category": "burs", "visual_kind": "scene", "image_queries": ["Zaragoza Spain city", " Spain university ", "", "zaragoza spain city", 5]}
    qs = photo.build_queries(post, cfg)
    assert qs[0] == "Zaragoza Spain city" and qs[1] == "Spain university" and qs[-1] == "university campus students"
    assert len(qs) == len(set(q.lower() for q in qs))
    assert photo.build_queries({"category": "yok", "visual_kind": "scene"}, cfg)[-1] == cfg["photo"]["fallback_queries"]["diger"]


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
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    calls = []

    def fake_get(url, params=None, headers=None, timeout=0):
        if "api.pexels.com" in url:
            calls.append(params["query"])
            assert headers["Authorization"] == "k" and params["orientation"] == "portrait"
            return _Resp(js={"photos": [] if params["query"] == "nowhere" else [_pexels_raw(7, photographer="Ayşe Yılmaz")]})
        assert "w=1080" in url and "h=1350" in url and "fit=crop" in url   # tam kart boyutunda kırpım istenir
        return _Resp(content=b"\xff\xd8" + b"x" * 30000, ctype="image/jpeg")

    monkeypatch.setattr(photo.requests, "get", fake_get)
    bg = photo.find_background({"id": "a", "category": "burs", "image_queries": ["nowhere", "spain city"]}, cfg)
    assert calls == ["nowhere", "spain city"]
    assert bg["credit"] == "Ayşe Yılmaz / Pexels" and bg["query"] == "spain city" and bg["bytes"].startswith(b"\xff\xd8")


def test_find_background_is_none_without_key_or_on_errors(cfg, monkeypatch):
    from diploport import photo
    post = {"id": "a", "category": "burs", "image_queries": ["x"]}
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert photo.find_background(post, cfg) is None             # anahtar yok
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(photo.requests, "get", lambda *a, **k: _Resp(status=429))
    assert photo.find_background(post, cfg) is None             # kota/hata: hat durmaz, fotoğrafsız devam
    cfg2 = dict(cfg, photo=dict(cfg["photo"], enabled=False))
    assert photo.find_background(post, cfg2) is None


def test_unsplash_normalization_and_filters(cfg):
    from diploport import photo
    n = photo.normalize_unsplash(_unsplash_raw("p1"))
    assert n["urls"][0].startswith("https://images.unsplash.com/photo-p1?ixid=abc&") and "w=1080" in n["urls"][0] and "h=1350" in n["urls"][0]
    assert n["credit"] == "Can Demir / Unsplash" and "utm_source=diploport" in n["url"] and n["track"].endswith("download?ixid=abc")
    good = photo.usable([n, photo.normalize_unsplash(_unsplash_raw("p2", w=900, h=1300)),
                         photo.normalize_unsplash(_unsplash_raw("p3", color="#050505"))], cfg)
    assert [g["id"] for g in good] == ["p1"]


def test_find_background_uses_unsplash_first_and_reports_download(cfg, monkeypatch):
    from diploport import photo
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "uk")
    monkeypatch.setenv("PEXELS_API_KEY", "pk")
    seen = []

    def fake_get(url, params=None, headers=None, timeout=0):
        seen.append(url)
        if url.startswith("https://api.unsplash.com/search/photos"):
            assert headers["Authorization"] == "Client-ID uk" and params["orientation"] == "portrait"
            return _Resp(js={"results": [_unsplash_raw("p9", name="Ayşe Yılmaz")]})
        if "api.unsplash.com/photos/p9/download" in url:
            assert headers["Authorization"] == "Client-ID uk"
            return _Resp(js={"url": "x"})
        assert "api.pexels.com" not in url
        return _Resp(content=b"\xff\xd8" + b"x" * 30000, ctype="image/jpeg")

    monkeypatch.setattr(photo.requests, "get", fake_get)
    bg = photo.find_background({"id": "a", "category": "burs", "image_queries": ["spain city"]}, cfg)
    assert bg["credit"] == "Ayşe Yılmaz / Unsplash" and bg["provider"] == "unsplash"
    assert any("/photos/p9/download" in u for u in seen)           # Unsplash kuralı: kullanım bildirimi


def test_find_background_falls_to_pexels_when_unsplash_key_rejected(cfg, monkeypatch):
    from diploport import photo
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "bad")
    monkeypatch.setenv("PEXELS_API_KEY", "pk")
    unsplash_calls = []

    def fake_get(url, params=None, headers=None, timeout=0):
        if url.startswith("https://api.unsplash.com/search"):
            unsplash_calls.append(params["query"])
            return _Resp(status=401)
        if "api.pexels.com" in url:
            return _Resp(js={"photos": [_pexels_raw(3, photographer="Ada")]})
        return _Resp(content=b"\xff\xd8" + b"x" * 30000, ctype="image/jpeg")

    monkeypatch.setattr(photo.requests, "get", fake_get)
    bg = photo.find_background({"id": "a", "category": "burs", "image_queries": ["a", "b", "c"]}, cfg)
    assert bg["provider"] == "pexels" and len(unsplash_calls) == 1     # 401'de aynı sağlayıcıda sorgu tekrarlanmaz


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
    post = {"title_tr": "Almanya'da Erasmus+ Gençlik Çalışanları Eğitimi", "category": "egitim", "visual_kind": "scene",
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
    assert "image_queries" in judge.PROMPT and "stock photo" in judge.PROMPT


# --- Gemini yedek model ---------------------------------------------------------

class _G:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._p = status, text, payload

    def json(self):
        return self._p


def _ok(d):
    import json as _json
    return _G(200, payload={"candidates": [{"content": {"parts": [{"text": _json.dumps(d)}]}}]})


def _setup_gemini(cfg, monkeypatch, handler):
    judge._FAILED_UNTIL.clear()
    cfg["gemini"]["model"] = "ana"
    cfg["gemini"]["fallback_models"] = ["yedek1", "yedek2"]
    calls = []

    def fake_post(url, headers=None, json=None, timeout=0):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        return handler(model)
    monkeypatch.setattr(judge.requests, "post", fake_post)
    monkeypatch.setattr(judge.time, "sleep", lambda s: None)
    return calls


def test_gemini_overloaded_primary_falls_back_and_is_skipped_next_time(cfg, monkeypatch):
    calls = _setup_gemini(cfg, monkeypatch, lambda m: _G(503, "high demand") if m == "ana" else _ok({"x": m}))
    assert judge.call_gemini("p", cfg, "key") == {"x": "yedek1"}
    assert calls == ["ana", "ana", "ana", "yedek1"]            # ana 3 deneme, sonra yedek
    calls.clear()
    assert judge.call_gemini("p", cfg, "key") == {"x": "yedek1"}
    assert calls == ["yedek1"]                                  # yoğun model 10 dk atlanır: boşuna beklenmez
    judge._FAILED_UNTIL.clear()


def test_gemini_missing_model_404_moves_on_and_all_failing_raises(cfg, monkeypatch):
    calls = _setup_gemini(cfg, monkeypatch, lambda m: _G(404) if m == "yedek1" else _G(503, "busy"))
    with pytest.raises(judge.GeminiError):
        judge.call_gemini("p", cfg, "key")
    assert set(calls) == {"ana", "yedek1", "yedek2"}
    judge._FAILED_UNTIL.clear()


def test_gemini_key_or_permission_error_does_not_try_fallbacks(cfg, monkeypatch):
    calls = _setup_gemini(cfg, monkeypatch, lambda m: _G(403, "API key not valid"))
    with pytest.raises(judge.GeminiError):
        judge.call_gemini("p", cfg, "key")
    assert calls == ["ana"]
    judge._FAILED_UNTIL.clear()


# --- Kanıt cümlesi uygunluktan söz etmeli ------------------------------------------

def test_evidence_must_talk_about_eligibility():
    from diploport.judge import evidence_mentions_eligibility as ok
    assert ok("Applications are open to international students from all nationalities")
    assert ok("Katılımcı ülkeler: Hollanda, İtalya, Türkiye, Macaristan")
    assert ok("Türk Vatandaşları için iyi bir fırsat")
    assert not ok("The fellowship supports young professionals who want to develop innovative solutions to water-related problems.")
    assert not ok("The European program for international students will help participants develop practical skills")
    assert not ok("The fellowship will run from 16 August to 22 October 2027, with in-person sessions in Berlin.")
    assert not ok("Erasmus+ Gençlik Değişimi") and not ok("")


def test_yes_with_irrelevant_but_verbatim_evidence_goes_to_review(art):
    sentence = next(x.strip() for x in art.text.split(".") if len(x.strip()) > 25 and not judge.evidence_mentions_eligibility(x))
    v = judge.decide(base_result(eligibility_evidence=sentence), ITEM, art, TODAY)
    assert v["status"] == "unclear" and "söz etmiyor" in v["reason"]


# --- Elementor sayfalarında düğme linkleri gürültü sayılıp silinmemeli -----------------

ELEMENTOR_PAGE = """<html><head><title>SI Pioneering Women in STEM</title></head><body>
<div class="elementor elementor-21079 elementor-location-single">
 <section class="elementor-section"><div class="elementor-widget-wrap elementor-element-populated">
   <p>""" + ("Pioneering Women in STEM scholarship for master's studies in Sweden. " * 12) + """</p>
   <div class="elementor-element elementor-button-info"><a class="elementor-button" href="https://si.se/en/apply/scholarships/pioneeringwomeninstem/"><span>Apply Now</span></a></div>
 </div></section>
 <div class="sharedaddy share-buttons"><a href="https://twitter.com/intent/tweet?x=1">Tweet</a></div>
 <div class="related-posts"><a href="https://example.org/other-scholarship">Related</a></div>
</div>
<article class="elementor-post"><h3>Related card</h3></article>
</body></html>"""


def test_elementor_apply_button_survives_noise_filter(cfg):
    a = article.parse_article(ELEMENTOR_PAGE, "https://www.opportunitiescircle.com/si-pioneering/", cfg)
    urls = [l["url"] for l in a.links]
    assert "https://si.se/en/apply/scholarships/pioneeringwomeninstem/" in urls
    assert not any("example.org" in u or "twitter.com" in u for u in urls)   # gerçek gürültü hâlâ silinir


# --- Kariyer Kapısı ---------------------------------------------------------------

KK_RSS = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>Kariyer Kapısı</title>
<item><guid>https://kariyerkapisi.gov.tr/IlanDetay?i=aaa</guid><link>https://kariyerkapisi.gov.tr/IlanDetay?i=aaa</link>
<category>Sözleşmeli Personel İlanları</category><title>KURUM A - SÖZLEŞMELİ BİLİŞİM PERSONELİ</title><pubDate>{now}</pubDate></item>
<item><guid>https://kariyerkapisi.gov.tr/IlanDetay?i=bbb</guid><link>https://kariyerkapisi.gov.tr/IlanDetay?i=bbb</link>
<category>Görevde Yükselme ve Unvan Değişikliği İlanları</category><title>KURUM B - GÖREVDE YÜKSELME</title><pubDate>{now}</pubDate></item>
</channel></rss>"""


def test_feed_skip_categories_and_unique_ids_per_guid(cfg, monkeypatch):
    from email.utils import format_datetime
    from datetime import datetime, timezone
    from diploport import sources
    body = KK_RSS.format(now=format_datetime(datetime.now(timezone.utc))).encode()
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: type("R", (), {
        "content": body, "headers": {"content-type": "application/rss+xml"}, "raise_for_status": lambda s: None})())
    src = next(s for s in cfg["sources"] if s["name"] == "Kariyer Kapısı")
    items = sources.fetch_feed(src)
    assert [i["title"] for i in items] == ["KURUM A - SÖZLEŞMELİ BİLİŞİM PERSONELİ"]   # görevde yükselme baştan elendi
    assert items[0]["source_cfg"]["fetcher"] == "kariyerkapisi" and items[0]["source_cfg"]["assume_eligible"]
    assert items[0]["id"] != sources.fetch_feed(dict(src, skip_categories=[]))[1]["id"]   # ?i= farklı → farklı kimlik


def test_kariyerkapisi_bbcode_to_text():
    from diploport.kariyerkapisi import bbcode_to_text
    raw = "[justify][size=14pt][b] TMSF Personel Alım İlanı[/b][/size][/justify][br][list][*]KPSS P3 puanı&nbsp;en az 70[*]Türk vatandaşı olmak[/list]"
    out = bbcode_to_text(raw)
    assert "[" not in out and "TMSF Personel Alım İlanı" in out
    assert out.splitlines()[-2:] == ["KPSS P3 puanı en az 70", "Türk vatandaşı olmak"]


def test_kariyerkapisi_fetch_article_builds_text_from_api(cfg, monkeypatch):
    from diploport import kariyerkapisi
    posted = {}

    def fake_post(url, json=None, headers=None, timeout=0):
        posted.update(url=url, json=json)
        return type("R", (), {"status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: {
            "ilanBaslik": "TMSF - FON UZMAN YARDIMCILIĞI", "kurumAdi": "TASARRUF MEVDUATI SİGORTA FONU", "ilanTuru": "A Grubu Memur",
            "ilN_NO": "ILN1", "basTarih": "2026-10-12T08:00:00", "bitTarih": "2026-10-30T17:00:00",
            "ilanMetni": "[b]Şartlar[/b][br]Türk vatandaşı olmak"}})()

    monkeypatch.setattr(kariyerkapisi.requests, "post", fake_post)
    art = kariyerkapisi.fetch_article({"link": "https://kariyerkapisi.gov.tr/IlanDetay?i=abc-123", "title": "ham başlık"}, cfg)
    assert posted["json"] == {"ilanGuid": "abc-123"} and posted["url"].startswith("https://api.kariyerkapisi.gov.tr/")
    assert art.title == "TMSF - FON UZMAN YARDIMCILIĞI" and art.links == []
    assert "Son başvuru tarihi: 2026-10-30" in art.text and "Türk vatandaşı olmak" in art.text and "[b]" not in art.text
    monkeypatch.setattr(kariyerkapisi.requests, "post", lambda *a, **k: type("R", (), {"status_code": 204})())
    with pytest.raises(ValueError):
        kariyerkapisi.fetch_article({"link": "https://kariyerkapisi.gov.tr/IlanDetay?i=abc-123", "title": "x"}, cfg)


def test_kariyerkapisi_falls_back_to_rss_details_when_api_unreachable(cfg, monkeypatch):
    import requests as rq
    from diploport import kariyerkapisi
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise rq.ConnectTimeout("zaman aşımı")

    monkeypatch.setattr(kariyerkapisi.requests, "post", boom)
    monkeypatch.setattr(kariyerkapisi, "_API_UNREACHABLE", False)
    item = {"link": "https://kariyerkapisi.gov.tr/IlanDetay?i=a1", "title": "KURUM - UZMAN YARDIMCILIĞI SINAVI",
            "category": "A Grubu Memur (Kariyer Meslek)", "published": "2026-10-12T05:00:00+00:00"}
    a = kariyerkapisi.fetch_article(item, cfg)
    b = kariyerkapisi.fetch_article(dict(item, link="https://kariyerkapisi.gov.tr/IlanDetay?i=a2"), cfg)
    assert a.degraded and b.degraded and len(calls) == 1          # API bir kez denenir, sonra vazgeçilir
    assert "UZMAN YARDIMCILIĞI SINAVI" in a.text and "A Grubu Memur" in a.text and "2026-10-12" in a.text
    assert "vatandaşlarına yöneliktir" in a.text
    monkeypatch.setattr(kariyerkapisi, "_API_UNREACHABLE", False)


# --- Kuruma özel kart: LOGO (stok/şehir fotoğrafı yok) ------------------------------------------

import io  # noqa: E402


def _png(color=(30, 44, 85), size=(240, 120), alpha=255):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGBA", size, color + (alpha,)).save(b, "PNG")
    return b.getvalue()


INST = {"id": "k1", "category": "is", "title_tr": "Kamu İhale Kurumu Uzman Yardımcılığı Giriş Sınavı", "country_tr": "Türkiye",
        "funding_tr": "Belirtilmemiş", "visual_kind": "institution", "icon": "landmark", "organization": "Kamu İhale Kurumu"}


def test_institution_card_without_logo_is_not_made_unless_icon_fallback_enabled(cfg, monkeypatch, tmp_path):
    from diploport import card, orgvisual, photo
    html_ = card.build_html(INST, cfg, None)
    assert 'class="plain"' in html_ and "M3 10l9-6.5 9 6.5z" in html_          # eski simge kartı hâlâ üretilebilir (yalnızca isteğe bağlı)
    assert card.icon_for({"category": "staj"}) == "briefcase" and card.icon_for({"category": "x", "icon": "yok"}) == "plane"
    monkeypatch.setattr(orgvisual, "find_org_visual", lambda *a, **k: None)
    monkeypatch.setattr(orgvisual, "hints_for", lambda *a, **k: {})
    monkeypatch.setattr(photo, "find_background", lambda *a, **k: (_ for _ in ()).throw(AssertionError("stok/şehir fotoğrafı aranmamalı")))
    with pytest.raises(card.NoOrgVisual):
        card.render_card(dict(INST), cfg, tmp_path / "k.jpg")
    assert not (tmp_path / "k.jpg").exists()
    cfg2 = dict(cfg, org_visual={"enabled": True, "when_missing": "icon"})
    post = dict(INST)
    out = card.render_card(post, cfg2, tmp_path / "k2.jpg")
    assert out.read_bytes()[:3] == b"\xff\xd8\xff" and "photo_credit" not in post


def test_org_name_matching_is_strict_but_turkish_aware():
    from diploport.orgvisual import name_matches as m, fold
    assert fold("KAMU İHALE KURUMU") == ["kamu", "ihale", "kurumu"] and fold("Oyak-Renault") == ["oyak", "renault"]
    assert m("KAMU İHALE KURUMU", "Kamu İhale Kurumu", 1)
    assert m("Oyak Renault Otomobil Fabrikaları", "Oyak-Renault", 1)                   # sayfa adı kurum adının kısa hâli
    assert not m("Orta Anadolu Kalkınma Ajansı", "Kalkınma Ajansları Genel Müdürlüğü", 1)   # genel sayfa: reddedilir
    assert not m("Türkiye İş Kurumu", "Türkiye İş Bankası", 2)
    assert m("UNHCR", "Birleşmiş Milletler Mülteciler Yüksek Komiserliği", 1, "... Mülteciler Yüksek Komiserliği (UNHCR) ...")
    assert m("UNHCR", "Birleşmiş Milletler Mülteciler Yüksek Komiserliği", 1, "United Nations High Commissioner for Refugees")  # BÜYÜK HARFLİ kısaltma
    assert not m("UNHCR", "Birleşmiş Milletler Mülteciler Yüksek Komiserliği", 2, "... (UNHCR) ...")   # sadece ilk sonuç
    assert not m("Keyo", "Pierre Maguelon", 1, "Fransız aktör")                                       # küçük harfli marka: tahmin yok
    assert not m("Keyo People", "Pierre Maguelon", 1, "Fransız aktör")
    assert m("UNHCR", "UNHCR", 3)                                                                     # birebir aynı ad: sıra önemsiz


def test_org_visual_classification():
    from diploport.orgvisual import classify
    assert classify("Kamu_İhale_Kurumu_logo.svg", 170, 283) == "logo" and classify("Renault_2021.svg", 954, 1255) == "logo"
    assert classify("UNHCR-geneveNov2013.jpg", 2304, 1665) == "photo"
    assert classify("Ferruhyildiz.jpg", 578, 545) is None                      # küçük fotoğraf (kişi vb.) kullanılmaz
    assert classify("Refugee_population_map.svg", 850, 557) is None            # harita/diyagram kullanılmaz


def test_image_validation_and_light_logo_detection():
    from diploport import orgvisual as ov
    assert ov.norm_image(b"<html>404</html>") is None and ov.norm_image(b"") is None
    assert ov.norm_image(_png(size=(40, 40)), min_side=100) is None                # çok küçük favicon
    assert ov.norm_image(_png(size=(2000, 100)), min_side=50) is None              # aşırı yatay şerit
    assert ov.norm_image(_png(size=(300, 120)), min_side=100) is not None
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 50"><rect fill="#1E2C55" width="200" height="50"/></svg>'
    assert ov.norm_image(svg) == svg and ov.image_size(svg) == (200.0, 50.0)
    # beyaz logo (saydam zeminli) -> lacivert panel; koyu logo ya da beyaz zeminli JPEG -> beyaz panel
    assert ov.too_light(_png((255, 255, 255), alpha=0) ) is True                    # tamamen saydam: görünmez
    from PIL import Image
    im = Image.new("RGBA", (100, 40), (0, 0, 0, 0))
    for x in range(20, 80):
        for y in range(10, 30):
            im.putpixel((x, y), (255, 255, 255, 255))
    b = io.BytesIO(); im.save(b, "PNG")
    assert ov.too_light(b.getvalue())
    assert not ov.too_light(_png((30, 44, 85), alpha=255))                         # opak: zemin kendi içinde
    jb = io.BytesIO(); Image.new("RGB", (80, 80), (255, 255, 255)).save(jb, "JPEG")
    assert not ov.too_light(jb.getvalue())                                         # beyaz zeminli JPEG logo beyaz panelde kalır
    assert ov.too_light(b'<svg viewBox="0 0 10 10"><path fill="#fff" d="M0 0h10v10z"/><path fill="white" d="M1 1"/></svg>')
    assert not ov.too_light(b'<svg viewBox="0 0 10 10"><path fill="#123" d="M0 0h10v10z"/></svg>')


def _site(title, extra=""):
    return f"<html><head><title>{title}</title>{extra}</head><body><header><a class='navbar-brand'><img src='/img/logo.svg' alt='logo'></a></header></body></html>"


def test_site_must_match_the_organization_before_its_logo_is_used():
    from diploport.orgvisual import site_matches as sm
    assert sm("Akçansa", _site("Akçansa | Çimento ve Beton"), "https://www.akcansa.com.tr/")
    assert sm("Orta Anadolu Kalkınma Ajansı", _site("ORAN - Orta Anadolu Kalkınma Ajansı"), "https://oran.org.tr/")
    assert sm("UNHCR", _site("Home"), "https://www.unhcr.org/")                    # alan adında geçiyor
    assert not sm("Keyo People", _site("Satılık Alan Adı - domain parking"), "https://www.keyo.com/")
    assert not sm("Orta Anadolu Kalkınma Ajansı", _site("Orta Anadolu Spor Kulübü"), "https://x.org/")   # kelimelerin yarısı yetmez


def test_logo_candidates_prefer_jsonld_then_page_logo_then_touch_icon_and_skip_partner_logos():
    from diploport.orgvisual import logo_candidates
    doc = """<html><head>
      <script type="application/ld+json">{"@type":"Organization","logo":{"@type":"ImageObject","url":"https://cdn.x.com/org.png"}}</script>
      <link rel="icon" href="/favicon.ico"><link rel="apple-touch-icon" sizes="180x180" href="/touch.png">
      <meta property="og:image" content="/img/hero-photo.jpg">
    </head><body>
      <div class="partners"><img src="/p/partner-logo.png" alt="partner logo"></div>
      <a class="navbar-brand"><img src="/assets/site-logo.svg" alt="Org"></a>
    </body></html>"""
    c = logo_candidates(doc, "https://www.x.com/tr/")
    assert c[0] == "https://cdn.x.com/org.png" and c[1] == "https://www.x.com/assets/site-logo.svg"
    assert "https://www.x.com/touch.png" in c and all("partner" not in u and "favicon" not in u and "hero" not in u for u in c)


def test_youthall_page_gives_company_logo_and_site():
    from diploport import orgvisual as ov
    page = ('<img src="https://cdn.youthall.com/media/images/company/image/9_x.jpg" class="bg-white">'
            '<img src="https://cdn.youthall.com/media/images/company/image/1859_x.jpg" alt="Akçansa logo">'
            '<a href="https://blog.youthall.com">b</a><a href="http://www.akcansa.com.tr">site</a><a href="https://x.com/youthallcom">t</a>')
    orig = ov._page
    ov._page = lambda url, timeout=12: (url, page)
    try:
        h = ov.source_hints("https://www.youthall.com/tr/Akcansa/x_58/", "Akçansa")
        assert h["logo_url"].endswith("1859_x.jpg") and h["sites"] == ["http://www.akcansa.com.tr/"]   # alt metni kurumla eşleşen görsel seçilir
        assert h["org_name"] == "Akçansa"
        assert ov.resolve_org("Akaryakıt Dışı Gelirler", "Shell") == "Shell" and ov.resolve_org("Aygaz", "Aygaz'da Kariyer") == "Aygaz"
        assert ov.resolve_org("", "Henkel") == "Henkel" and ov.resolve_org("Youthall", "Henkel") == "Henkel" and ov.resolve_org("X", "") == "X"
        assert ov.source_hints("https://www.erasmusgram.com/x/", "Akçansa") == {}
    finally:
        ov._page = orig


def test_wikidata_lookup_checks_name_and_returns_logo_and_site(monkeypatch):
    from diploport import orgvisual as ov
    ent = {"Q1": {"labels": {"tr": {"value": "Aygaz"}}, "descriptions": {"tr": {"value": "Türk doğalgaz şirketi"}}, "aliases": {},
                  "claims": {"P154": [{"mainsnak": {"datavalue": {"value": "Aygaz logo.svg"}}, "rank": "normal"}],
                             "P856": [{"mainsnak": {"datavalue": {"value": "http://www.aygaz.com.tr/en-US/Corporate/"}}, "rank": "normal"}]}},
           "Q2": {"labels": {"en": {"value": "Aygaz 3"}}, "descriptions": {"en": {"value": "ship built in 1976"}}, "aliases": {}, "claims": {"P154": [
               {"mainsnak": {"datavalue": {"value": "gemi.png"}}, "rank": "normal"}]}}}

    def fake_api(host, params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q2"}, {"id": "Q1"}]}
        return {"entities": ent}

    monkeypatch.setattr(ov, "_api", fake_api)
    r = ov.wikidata_lookup("Aygaz")
    assert r == {"logo_file": "Aygaz logo.svg", "site": "http://www.aygaz.com.tr/en-US/Corporate/"}   # gemi girdisi elendi
    ent["Q1"]["labels"] = {"tr": {"value": "Başka Şirket"}}
    assert ov.wikidata_lookup("Aygaz") == {"logo_file": None, "site": None}                          # ad tutmuyor: kullanılmaz


def test_find_org_visual_order_and_never_uses_stock(cfg, monkeypatch, tmp_path):
    from diploport import orgvisual as ov
    logo = _png()
    calls = []
    monkeypatch.setattr(ov, "ORG_LOGO_DIR", tmp_path / "logos")
    monkeypatch.setattr(ov, "_fetch", lambda url, min_side=100, timeout=20: (calls.append(url), logo if "good" in url else None)[1])
    monkeypatch.setattr(ov, "_wikipedia", lambda org: (calls.append("wiki"), (None, None))[1])
    monkeypatch.setattr(ov, "wikidata_lookup", lambda org: (calls.append("wd"), {"logo_file": None, "site": "https://www.org.com/x"})[1])
    monkeypatch.setattr(ov, "site_logo", lambda org, site: (calls.append("site:" + site), None)[1])
    monkeypatch.setattr(ov, "_page", lambda url, timeout=12: None)
    # 1) kaynak sayfa logosu ilk sırada: Vikipedi'ye hiç gidilmez
    r = ov.find_org_visual("Org", cfg, {"logo_url": "https://good/l.png"})
    assert r["kind"] == "logo" and r["bytes"] == logo and "wiki" not in calls
    # 2) logo yoksa: Vikipedi -> Wikidata -> siteler (ipucu + Wikidata sitesi) denenir; hiçbiri yoksa None
    calls.clear()
    assert ov.find_org_visual("Org", cfg, {"logo_url": "https://bad/l.png", "sites": ["org.org.tr"]}) is None
    assert calls == ["https://bad/l.png", "wiki", "wd", "site:https://org.org.tr/", "site:https://www.org.com/"]
    # 3) elle eklenen logo her şeyden önce gelir
    (tmp_path / "logos").mkdir()
    (tmp_path / "logos" / "orta-anadolu-kalkinma-ajansi.png").write_bytes(logo)
    calls.clear()
    r = ov.find_org_visual("ORTA ANADOLU KALKINMA AJANSI", cfg, {"logo_url": "https://good/l.png"})
    assert r["source"] == "elle eklenen logo" and calls == []
    assert ov.find_org_visual("", cfg) is None
    calls.clear()                                       # kurum adı yok ama kaynak sayfa logosu var: yine de kullanılır
    assert ov.find_org_visual("", cfg, {"logo_url": "https://good/l.png"})["bytes"] == logo and calls == ["https://good/l.png"]
    assert ov.find_org_visual("Org", {"org_visual": {"enabled": False}}) is None


def test_wikipedia_logo_from_page_images_and_photo_licence(cfg, monkeypatch):
    from diploport import orgvisual as ov
    img = _png(size=(600, 300))
    state = {"page": {"index": 1, "title": "Orman Genel Müdürlüğü", "pageimage": "OGM_binasi.jpg", "extract": "",
                      "original": {"width": 3000, "height": 2000}, "thumbnail": {"source": "https://upload.wikimedia.org/ogm_binasi.jpg"}},
             "images": [{"title": "Dosya:Commons-logo.svg"}, {"title": "Dosya:OGM logo.svg"}], "lic": {}}

    def fake_api(host, params):
        if params.get("generator") == "search":
            return {"query": {"pages": {"1": state["page"]}}}
        if params.get("prop") == "images":
            return {"query": {"pages": {"1": {"images": state["images"]}}}}
        if params.get("prop") == "imageinfo" and "iiurlwidth" in params:
            return {"query": {"pages": {"1": {"imageinfo": [{"thumburl": "https://upload.wikimedia.org/ogm_logo.png"}]}}}}
        return {"query": {"pages": {"1": {"imageinfo": [{"extmetadata": state["lic"]}]}}}}

    monkeypatch.setattr(ov, "_api", fake_api)
    monkeypatch.setattr(ov, "_fetch", lambda url, min_side=100, timeout=20: img if "ogm_logo" in url else None)
    # ana görsel bina fotoğrafı, ama sayfada logo dosyası var -> logo tercih edilir (Commons-logo.svg gibi şablon dosyaları atlanır)
    found, photo = ov._wikipedia("Orman Genel Müdürlüğü")
    assert found["kind"] == "logo" and found["bytes"] == img and found["source"].startswith("Vikipedi (tr)")
    # logo dosyası yoksa fotoğraf yalnızca serbest lisansla kullanılır, yazar/lisans künyesi yazılır
    state["images"] = []
    found, photo = ov._wikipedia("Orman Genel Müdürlüğü")
    assert found is None and photo["fname"] == "OGM_binasi.jpg"
    state["lic"] = {"Artist": {"value": '<a href="x">Ada Yılmaz</a>'}, "LicenseShortName": {"value": "CC BY-SA 3.0"}}
    monkeypatch.setattr(ov.requests, "get", lambda *a, **k: type("R", (), {"status_code": 200, "headers": {"content-type": "image/jpeg"}, "content": b"x" * 30000})())
    v = ov._wikipedia_photo(photo)
    assert v["kind"] == "photo" and v["credit"] == "Ada Yılmaz / Wikimedia Commons (CC BY-SA 3.0)"
    state["lic"] = {"LicenseShortName": {"value": "Fair use"}, "NonFree": {"value": "1"}}
    assert ov._wikipedia_photo(photo) is None
    # kurum adı tutmuyorsa hiçbir şey kullanılmaz
    state["page"]["title"] = "Kalkınma Ajansları Genel Müdürlüğü"
    assert ov._wikipedia("Orta Anadolu Kalkınma Ajansı") == (None, None)


def test_card_puts_logo_on_panel_dark_for_white_logo_and_never_searches_stock(cfg, monkeypatch, tmp_path):
    from diploport import card, orgvisual, photo
    monkeypatch.setattr(orgvisual, "hints_for", lambda *a, **k: {})
    monkeypatch.setattr(photo, "find_background", lambda *a, **k: (_ for _ in ()).throw(AssertionError("stok aranmamalı")))
    dark_logo = _png((30, 44, 85), size=(300, 150))
    monkeypatch.setattr(orgvisual, "find_org_visual", lambda *a, **k: {"kind": "logo", "bytes": dark_logo, "credit": "",
                                                                    "title": "Kamu İhale Kurumu", "source": "Vikipedi (tr)", "url": "https://tr.wikipedia.org/wiki/x"})
    post = dict(INST)
    assert 'class="panel"' in card.build_html(post, cfg, None, dark_logo)
    card.render_card(post, cfg, tmp_path / "a.jpg")
    assert post["photo_query"] == "logo: Kamu İhale Kurumu (Vikipedi (tr))" and "photo_credit" not in post
    assert 'class="panel dark"' in card.build_html(post, cfg, None, _png((255, 255, 255), alpha=0))
    # boyut: oran korunur, küçük raster en fazla 2.5x büyür, SVG panele sığacak kadar büyür
    assert card._logo_style(_png(size=(100, 50))) == "width:250px;height:125px"
    assert card._logo_style(_png(size=(1000, 100))) == "width:500px;height:50px"
    assert card._logo_style(b'<svg viewBox="0 0 100 100"></svg>') == "width:310px;height:310px"


def test_photo_search_has_no_logo_zone_rule_and_no_institution_fallback(cfg):
    from diploport import photo
    assert not hasattr(photo, "logo_zone_ok")
    assert "logo_zone_min_luma" not in cfg["photo"] and "institution_fallback" not in cfg["photo"]
    qs = photo.build_queries({"category": "burs", "visual_kind": "scene", "image_queries": ["Berlin city"]}, cfg)
    assert qs[-1] == "university campus students"


def test_find_background_accepts_dark_top_left_photo(cfg, monkeypatch):
    from PIL import Image
    from diploport import photo
    b = io.BytesIO(); Image.new("RGB", (1200, 1600), (20, 25, 40)).save(b, "JPEG", quality=90)
    dark_top_left = b.getvalue() + b"0" * 25000
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "uk"); monkeypatch.delenv("PEXELS_API_KEY", raising=False)

    def fake_get(url, params=None, headers=None, timeout=0):
        if url.startswith("https://api.unsplash.com/search"):
            return _Resp(js={"results": [_unsplash_raw("p0", color="#aab4c8")]})
        if "api.unsplash.com/photos" in url:
            return _Resp(js={})
        return _Resp(content=dark_top_left, ctype="image/jpeg")

    monkeypatch.setattr(photo.requests, "get", fake_get)
    bg = photo.find_background({"id": "z", "category": "burs", "visual_kind": "scene", "image_queries": ["x"]}, cfg)
    assert bg and bg["bytes"] == dark_top_left      # sol üst logo bölgesi koyu diye atlanmaz: kural kaldırıldı
