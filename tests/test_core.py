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


# --- Kuruma özel sade kart ---------------------------------------------------------

def test_institution_card_icon_layer_when_no_visual_found(cfg, monkeypatch, tmp_path):
    from diploport import card, orgvisual, photo
    post = {"id": "k1", "category": "is", "title_tr": "Kamu İhale Kurumu Uzman Yardımcılığı Giriş Sınavı", "country_tr": "Türkiye",
            "funding_tr": "Belirtilmemiş", "visual_kind": "institution", "icon": "landmark", "organization": "Kamu İhale Kurumu"}
    html_ = card.build_html(post, cfg, None)
    assert 'class="plain"' in html_ and "M3 10l9-6.5 9 6.5z" in html_          # sütunlu bina simgesi
    assert card.icon_for({"category": "staj"}) == "briefcase" and card.icon_for({"category": "x", "icon": "yok"}) == "plane"
    monkeypatch.setattr(orgvisual, "find_org_visual", lambda *a, **k: None)
    monkeypatch.setattr(photo, "find_background", lambda *a, **k: None)
    out = card.render_card(post, cfg, tmp_path / "k.jpg")
    assert out.read_bytes()[:3] == b"\xff\xd8\xff" and "photo_credit" not in post
    assert 'class="plain"' not in card.build_html(dict(post, visual_kind="scene"), cfg, b"\xff\xd8" + b"x" * 50)


def test_org_name_matching_is_strict_but_turkish_aware():
    from diploport.orgvisual import name_matches as m, fold
    assert fold("KAMU İHALE KURUMU") == ["kamu", "ihale", "kurumu"] and fold("Oyak-Renault") == ["oyak", "renault"]
    assert m("KAMU İHALE KURUMU", "Kamu İhale Kurumu", 1)
    assert m("Oyak Renault Otomobil Fabrikaları", "Oyak-Renault", 1)                   # sayfa adı kurum adının kısa hâli
    assert not m("Orta Anadolu Kalkınma Ajansı", "Kalkınma Ajansları Genel Müdürlüğü", 1)   # genel sayfa: reddedilir
    assert not m("Türkiye İş Kurumu", "Türkiye İş Bankası", 2)
    assert m("UNHCR", "Birleşmiş Milletler Mülteciler Yüksek Komiserliği", 1, "... Mülteciler Yüksek Komiserliği (UNHCR) ...")
    assert not m("UNHCR", "Birleşmiş Milletler Mülteciler Yüksek Komiserliği", 2, "... (UNHCR) ...")   # sadece ilk sonuç
    assert not m("Keyo People", "Pierre Maguelon", 1, "Fransız aktör")


def test_org_visual_classification():
    from diploport.orgvisual import classify
    assert classify("Kamu_İhale_Kurumu_logo.svg", 170, 283) == "logo" and classify("Renault_2021.svg", 954, 1255) == "logo"
    assert classify("UNHCR-geneveNov2013.jpg", 2304, 1665) == "photo"
    assert classify("Ferruhyildiz.jpg", 578, 545) is None                      # küçük fotoğraf (kişi vb.) kullanılmaz
    assert classify("Refugee_population_map.svg", 850, 557) is None            # harita/diyagram kullanılmaz


def _wiki_pages(title, fname, w, h, extract=""):
    return {"query": {"pages": {"1": {"index": 1, "title": title, "pageimage": fname, "extract": extract,
                                      "original": {"width": w, "height": h}, "thumbnail": {"source": "https://upload.wikimedia.org/x.png"}}}}}


def test_find_org_visual_logo_photo_and_licence_rules(cfg, monkeypatch):
    from diploport import orgvisual
    img = b"\x89PNG" + b"x" * 5000
    state = {"pages": _wiki_pages("Kamu İhale Kurumu", "Kamu_İhale_Kurumu_logo.svg", 170, 283), "lic": {}}

    def fake_get(url, params=None, headers=None, timeout=0):
        if "wikipedia.org/w/api.php" in url:
            if params.get("prop") == "imageinfo":
                return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"query": {"pages": {"1": {"imageinfo": [{"extmetadata": state["lic"]}]}}}}})()
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: state["pages"]})()
        return type("R", (), {"status_code": 200, "headers": {"content-type": "image/png"}, "content": img})()

    monkeypatch.setattr(orgvisual.requests, "get", fake_get)
    v = orgvisual.find_org_visual("KAMU İHALE KURUMU", cfg)
    assert v["kind"] == "logo" and v["credit"] == "" and v["lang"] == "tr" and v["url"].endswith("/Kamu_%C4%B0hale_Kurumu".replace("%C4%B0", "İ"))
    # fotoğraf: serbest lisans -> kullanılır, yazar/lisans künyesi yazılır
    state["pages"] = _wiki_pages("Orman Genel Müdürlüğü", "OGM_binasi.jpg", 3000, 2000)
    state["lic"] = {"Artist": {"value": '<a href="x">Ada Yılmaz</a>'}, "LicenseShortName": {"value": "CC BY-SA 3.0"}}
    v = orgvisual.find_org_visual("Orman Genel Müdürlüğü", cfg)
    assert v["kind"] == "photo" and v["credit"] == "Ada Yılmaz / Wikimedia Commons (CC BY-SA 3.0)"
    # fotoğraf: serbest olmayan lisans -> kullanılmaz
    state["lic"] = {"LicenseShortName": {"value": "Fair use"}, "NonFree": {"value": "1"}}
    assert orgvisual.find_org_visual("Orman Genel Müdürlüğü", cfg) is None
    # kurum adı tutmuyorsa kullanılmaz
    state["pages"] = _wiki_pages("Kalkınma Ajansları Genel Müdürlüğü", "x_logo.svg", 100, 100)
    assert orgvisual.find_org_visual("Orta Anadolu Kalkınma Ajansı", cfg) is None
    assert orgvisual.find_org_visual("", cfg) is None


def test_card_uses_logo_panel_then_falls_back_to_stock(cfg, monkeypatch, tmp_path):
    from diploport import card, orgvisual, photo
    post = {"id": "k2", "category": "is", "title_tr": "Kamu İhale Kurumu Uzman Yardımcılığı", "country_tr": "Türkiye",
            "visual_kind": "institution", "organization": "Kamu İhale Kurumu"}
    png = card.ROOT / "assets" / "logo.png"
    monkeypatch.setattr(orgvisual, "find_org_visual", lambda *a, **k: {"kind": "logo", "bytes": png.read_bytes(), "credit": "",
                                                                    "title": "Kamu İhale Kurumu", "lang": "tr", "url": "https://tr.wikipedia.org/wiki/x"})
    monkeypatch.setattr(photo, "find_background", lambda *a, **k: (_ for _ in ()).throw(AssertionError("stok aranmamalı")))
    assert 'class="panel"' in card.build_html(post, cfg, None, png.read_bytes())
    card.render_card(post, cfg, tmp_path / "a.jpg")
    assert post["photo_query"].startswith("logo: Kamu İhale Kurumu") and "photo_credit" not in post
    # kurum görseli yoksa sektöre uygun stok fotoğraf aranır
    monkeypatch.setattr(orgvisual, "find_org_visual", lambda *a, **k: None)
    monkeypatch.setattr(photo, "find_background", lambda *a, **k: {"bytes": png.read_bytes(), "credit": "Ada / Unsplash", "url": "u", "query": "government building"})
    post2 = dict(post, id="k3")
    card.render_card(post2, cfg, tmp_path / "b.jpg")
    assert post2["photo_credit"] == "Ada / Unsplash" and post2["photo_query"] == "government building"


def test_too_light_logo_detection_and_institution_fallback_query(cfg):
    import io
    from PIL import Image
    from diploport import orgvisual, photo
    def png(color, alpha=255):
        b = io.BytesIO(); Image.new("RGBA", (60, 30), color + (alpha,)).save(b, "PNG"); return b.getvalue()
    assert orgvisual.too_light(png((255, 255, 255))) and orgvisual.too_light(png((10, 10, 10), alpha=0))
    assert not orgvisual.too_light(png((30, 44, 85)))
    qs = photo.build_queries({"category": "is", "visual_kind": "institution", "image_queries": ["Kayseri city"]}, cfg)
    assert qs == ["Kayseri city", "Ankara city skyline"]
    qs = photo.build_queries({"category": "burs", "visual_kind": "scene", "image_queries": ["Berlin city"]}, cfg)
    assert qs[-1] == "university campus students"


def test_logo_zone_rejects_dark_or_busy_top_left_and_next_candidate_is_tried(cfg, monkeypatch):
    import io, random
    from PIL import Image
    from diploport import photo

    def jpg(img):
        b = io.BytesIO(); img.save(b, "JPEG", quality=90); return b.getvalue()

    light = jpg(Image.new("RGB", (1200, 1600), (200, 210, 225)))
    dark = jpg(Image.new("RGB", (1200, 1600), (20, 25, 40)))
    rnd = random.Random(1)
    noisy_im = Image.new("L", (1200, 1600))
    noisy_im.putdata([rnd.choice((10, 250)) for _ in range(1200 * 1600)])
    noisy = jpg(noisy_im.convert("RGB"))
    assert photo.logo_zone_ok(light) and not photo.logo_zone_ok(dark) and not photo.logo_zone_ok(noisy)
    assert photo.logo_zone_ok(b"bozuk veri")            # okunamazsa engellenmez

    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "uk"); monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    served = {"p0": dark, "p1": dark, "p2": light}

    def fake_get(url, params=None, headers=None, timeout=0):
        if url.startswith("https://api.unsplash.com/search"):
            return _Resp(js={"results": [_unsplash_raw(f"p{i}") for i in range(3)]})
        if "api.unsplash.com/photos" in url:
            return _Resp(js={})
        pid = url.split("photo-")[1].split("?")[0]
        return _Resp(content=served[pid] + b"0" * 25000, ctype="image/jpeg")

    monkeypatch.setattr(photo.requests, "get", fake_get)
    bg = photo.find_background({"id": "z", "category": "burs", "visual_kind": "scene", "image_queries": ["x"]}, cfg)
    assert bg and bg["bytes"].startswith(light[:100])   # iki koyu aday atlandı, açık olan seçildi
