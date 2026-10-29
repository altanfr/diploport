"""Ana akış. İki aşama:

  run  : Telegram kararlarını oku -> süresi dolanları ayıkla -> yeni ilanları tara/yargıla ->
         kotaya göre BİR ilanı seçip görselini üret, siteye ekle, "outbox"a koy.
  send : Görsel herkese açık URL'den görünür olunca Make webhook'u ile Instagram'a gönder.

Aralarında workflow `git commit && git push` yapar; böylece görsel GitHub Pages'te yayına girer.
"""
from __future__ import annotations

import copy
import logging
import os
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from . import kariyerkapisi, site
from .article import fetch_article, validate_link
from .card import render_card
from .judge import GeminiError, judge_item
from .publish import (build_caption, notify, poll_decisions, post_to_make, send_pending,
                      telegram_enabled, wait_public)
from . import sources as sources_mod
from .sources import collect_new
from .state import age_days, load_state, mark, save_state
from . import util
from .util import canonical_url, domain_of, norm_text, now_tr, now_utc

log = logging.getLogger("diploport.pipeline")


# --- yardımcılar -------------------------------------------------------------

def _is_duplicate_title(state: dict, title: str) -> bool:
    key = norm_text(title)
    return any(SequenceMatcher(None, key, t).ratio() >= 0.88 for t in state["titles"])


def _remember(state: dict, post: dict) -> None:
    state["official_seen"].append(canonical_url(post["apply_url"]))
    state["titles"].append(norm_text(post["title_tr"]))


def _enqueue(state: dict, post: dict) -> None:
    post["queued_at"] = now_utc().isoformat()
    state["queue"].append(post)
    mark(state, post["id"], "queued", "", post["title_tr"])


def _expire(state: dict, cfg: dict, today) -> None:
    lim = cfg["limits"]

    def dead(p: dict, ttl: int) -> str:
        dl = p.get("deadline")
        if dl and dl < today.isoformat():
            return "son başvuru geçti"
        if age_days(p.get("queued_at", "")) > ttl:
            return "bekleme süresi doldu"
        return ""

    keep = []
    for p in state["queue"]:
        why = dead(p, lim["queue_ttl_days"])
        if why:
            mark(state, p["id"], "expired", why, p["title_tr"])
        else:
            keep.append(p)
    state["queue"] = keep
    for pid, p in list(state["pending"].items()):
        why = dead(p, lim["pending_ttl_days"])
        if why:
            mark(state, pid, "expired", why, p["title_tr"])
            del state["pending"][pid]


# --- 1) tarama ---------------------------------------------------------------

def _detail(post: dict, evidence: str = "") -> str:
    """Rapor satırı için denetlenebilir özet: kanıt, son tarih, resmî link, fotoğraf arama ifadeleri."""
    parts = []
    if evidence:
        parts.append(f"kanıt: {evidence[:170]}")
    parts.append(f"son tarih: {post.get('deadline') or '-'}")
    if post.get("detail_missing"):
        parts.append("ayrıntı metni alınamadı: özet yalnızca başlıktan")
    parts.append(f"link: {post.get('apply_url', '-')}")
    qs = [q for q in (post.get("image_queries") or []) if isinstance(q, str)]
    if post.get("visual_kind") == "institution":
        parts.append("görsel: kuruma özel (stok foto yok, simge kartı)")
    elif qs:
        parts.append("foto: " + " / ".join(qs[:3]))
    return " · ".join(parts)


def _process_item(it: dict, cfg: dict, state: dict, today, dry: bool, report: list) -> str:
    """Tek ilanı işler. Dönüş: 'ok' | 'fail' (Gemini geçici hata verdi: ilan işaretlenmez, sonraki çalışmada yeniden denenir)."""
    title = it["title"]
    try:
        if it["source_cfg"].get("fetcher") == "kariyerkapisi":
            art = kariyerkapisi.fetch_article(it, cfg)   # ilan metni sitenin API'sinden gelir (ulaşılamazsa RSS künyesi)
        else:
            art = fetch_article(it["link"], cfg)
    except Exception as e:  # noqa: BLE001
        n = state["fails"].get(it["id"], 0) + 1
        state["fails"][it["id"]] = n
        if n >= 3:
            mark(state, it["id"], "rejected", f"sayfa açılamadı: {type(e).__name__}", title)
        report.append(("hata", it["source"], title, f"sayfa açılamadı ({n}/3): {type(e).__name__}"))
        return "ok"

    try:
        verdict = judge_item(it, art, cfg, today)
    except GeminiError as e:
        report.append(("hata", it["source"], title, f"Gemini: {str(e)[:160]} (atlandı, sonra yeniden denenecek)"))
        log.error("Gemini hatası, ilan atlandı: %s", e)
        return "fail"
    finally:
        time.sleep(cfg["gemini"].get("seconds_between_calls", 0))

    data, status = verdict["data"], verdict["status"]
    if status == "reject":
        mark(state, it["id"], "rejected", verdict["reason"], title)
        report.append(("elendi", it["source"], title, verdict["reason"]))
        return "ok"

    # Resmî başvuru linki: modelin seçtiği adayı doğrula; olmazsa (sadece izinli kaynaklarda) kaynak sayfası.
    official, note = None, ""
    if verdict["link"]:
        v = validate_link(verdict["link"]["url"], cfg)
        if v["ok"]:
            official = v["url"]
        else:
            note = v["reason"]
    if not official and it["source_cfg"].get("link_fallback_to_source"):
        v = validate_link(art.final_url, cfg, allow_domains={domain_of(art.final_url)})
        if v["ok"]:
            official = v["url"]
    if not official:
        doms = []
        for l in art.links:
            d = domain_of(l["url"])
            if d and d not in doms:
                doms.append(d)
        picked = "model link seçmedi" if not verdict["link"] else f"seçilen link reddedildi: {note or '?'}"
        reason = (f"resmî başvuru linki bulunamadı ({picked}; sayfada {len(art.links)} aday link"
                  + (f": {', '.join(doms[:6])}" if doms else "") + ")")
        mark(state, it["id"], "rejected", reason, title)
        report.append(("elendi", it["source"], title, reason))
        return "ok"

    post = {**data, "id": it["id"], "apply_url": official, "source": it["source"], "source_url": it["link"]}
    if art.degraded:
        post["detail_missing"] = True
    # Kuruma özel ilan: stok fotoğraf yok, sade kart + simge. Model karar verir; kaynak ayarı bunu zorlayabilir.
    post["visual_kind"] = it["source_cfg"].get("visual") or post.get("visual_kind") or "institution"
    if it["source_cfg"].get("icon"):
        post["icon"] = it["source_cfg"]["icon"]
    if it["source_cfg"].get("fetcher") == "kariyerkapisi" and " - " in title:
        post["organization"] = title.split(" - ", 1)[0].strip()   # "KAMU İHALE KURUMU - ..." -> kurum adı kesin
    if post.get("visual_kind") == "institution" and not post.get("organization"):
        post["organization"] = ""
    if canonical_url(official) in state["official_seen"] or _is_duplicate_title(state, post["title_tr"]):
        mark(state, it["id"], "rejected", "tekrar (başka kaynakta zaten işlendi)", title)
        report.append(("elendi", it["source"], title, "tekrar"))
        return "ok"

    needs_review = status == "unclear" or bool(post.get("fee_required"))
    if needs_review:
        reason = post.get("unclear_reason_tr") or ""
        if post.get("fee_required"):
            reason = (reason + " Katılım ücreti var.").strip()
        post["unclear_reason_tr"] = reason
        if dry:
            report.append(("onaya düşerdi", it["source"], post["title_tr"], f"{reason} · {_detail(post)}"))
        elif telegram_enabled():
            mid = send_pending(it, post)
            if mid is None:
                return "ok"  # gönderilemedi: bir sonraki çalışmada tekrar denenir
            post["message_id"] = mid
            post["queued_at"] = now_utc().isoformat()
            state["pending"][it["id"]] = post
            mark(state, it["id"], "pending", reason, title)
            _remember(state, post)
            report.append(("onayda", it["source"], post["title_tr"], f"{reason} · {_detail(post)}"))
        else:
            mark(state, it["id"], "rejected", f"onay kanalı yok: {reason}", title)
            report.append(("elendi", it["source"], title, f"belirsiz, Telegram ayarlı değil: {reason}"))
        return "ok"

    if dry:
        report.append(("kuyruğa girerdi", it["source"], post["title_tr"], _detail(post, data.get("eligibility_evidence", ""))))
    else:
        _enqueue(state, post)
        _remember(state, post)
        report.append(("kuyrukta", it["source"], post["title_tr"], _detail(post, data.get("eligibility_evidence", ""))))
    return "ok"


# --- 2) yayına hazırlık ------------------------------------------------------

def _prepare_next(state: dict, cfg: dict, now, today) -> str | None:
    lim = cfg["limits"]
    lo, hi = lim["publish_window_tr"]
    if not (lo <= now.hour < hi) or not state["queue"]:
        return None

    tz = now.tzinfo
    recent = state["published"] + state["outbox"]
    todays = [p for p in recent if datetime.fromisoformat(p["ts"]).astimezone(tz).date() == today]
    if len(todays) >= lim["max_posts_per_day"]:
        return None
    if recent:
        last = max(datetime.fromisoformat(p["ts"]) for p in recent)
        if now_utc() - last < timedelta(hours=lim["min_hours_between_posts"]):
            return None

    state["queue"].sort(key=lambda q: (q.get("deadline") or "9999-12-31", q["queued_at"]))
    post = state["queue"][0]
    image = util.DOCS_DIR / "images" / f"{post['id']}.jpg"
    try:
        render_card(post, cfg, image, today)
    except Exception as e:  # noqa: BLE001 - görsel üretilemediyse ilan kuyrukta kalsın, kaybolmasın
        log.error("Görsel üretilemedi (%s): %s", post["id"], e)
        notify(f"Diploport: görsel üretilemedi ({post['title_tr']}): {type(e).__name__}")
        return None
    state["queue"].pop(0)
    site.add_post(post, cfg["limits"]["site_max_posts"])
    site.prune_images()
    state["outbox"].append(
        {"id": post["id"], "ts": now_utc().isoformat(), "attempts": 0, "caption": build_caption(post, cfg),
         "title": post["title_tr"], "apply_url": post["apply_url"], "photo_query": post.get("photo_query", "")}
    )
    return post["id"]


def run(cfg: dict, dry: bool = False) -> dict:
    state = load_state()
    if dry:
        state = copy.deepcopy(state)
    now = now_tr(cfg)
    today = now.date()
    report: list[tuple] = []

    if not dry:
        for action, iid in poll_decisions(state):
            post = state["pending"].pop(iid, None)
            if post is None:
                continue
            if action == "ok":
                _enqueue(state, post)
                report.append(("onaylandı", post["source"], post["title_tr"], "Telegram"))
            else:
                mark(state, iid, "rejected", "elle reddedildi", post["title_tr"])
                report.append(("reddedildi", post["source"], post["title_tr"], "Telegram"))
    _expire(state, cfg, today)

    found = collect_new(cfg, state)
    items = found[: cfg["limits"]["max_judge_per_run"]]
    log.info("%d yeni ilan bulundu, %d tanesi işlenecek", len(found), len(items))
    fails = 0
    for it in items:
        if _process_item(it, cfg, state, today, dry, report) == "fail":
            fails += 1
            if fails >= 2:   # Gemini art arda hata veriyor (kota/yoğunluk): boşuna bekleme, sonra devam
                log.error("Gemini art arda hata verdi, çalışma durduruluyor")
                break
        else:
            fails = 0

    prepared = None if dry else _prepare_next(state, cfg, now, today)
    if not dry:
        save_state(state)
        site.write_site_config(cfg)
    _write_report(report, state, prepared, dry, now, found=len(found), limit=len(items))
    return {"processed": len(items), "prepared": prepared, "queue": len(state["queue"]),
            "pending": len(state["pending"]), "report": report}


# --- 3) gönderim -------------------------------------------------------------

def send(cfg: dict) -> int:
    state = load_state()
    site_url = cfg["brand"]["site_url"].rstrip("/")
    sent = 0
    for ob in list(state["outbox"]):
        url = f"{site_url}/images/{ob['id']}.jpg"
        ob["attempts"] += 1
        if not wait_public(url):
            log.error("Görsel yayında görünmedi: %s", url)
            if ob["attempts"] >= 3:
                notify(f"Diploport: görsel yayında görünmüyor ({ob['title']}). GitHub Pages ayarını kontrol et.")
            continue
        try:
            post_to_make({"id": ob["id"], "image_url": url, "caption": ob["caption"],
                          "title": ob["title"], "apply_url": ob["apply_url"]})
        except Exception as e:  # noqa: BLE001
            log.error("Make gönderimi başarısız: %s", e)
            if ob["attempts"] >= 3:
                notify(f"Diploport: Instagram'a gönderilemedi ({ob['title']}): {e}")
            continue
        state["outbox"].remove(ob)
        state["published"].append({"id": ob["id"], "ts": now_utc().isoformat(), "official": ob["apply_url"],
                                   "photo_query": ob.get("photo_query", "")})
        mark(state, ob["id"], "published", "", ob["title"])
        sent += 1
    save_state(state)
    return sent


# --- rapor -------------------------------------------------------------------

def _write_report(report: list[tuple], state: dict, prepared: str | None, dry: bool, now,
                  found: int = 0, limit: int = 0) -> None:
    lines = [f"# Son çalışma ({now:%Y-%m-%d %H:%M} TR){' — DRY RUN' if dry else ''}", ""]
    lines.append(f"Kuyruk: {len(state['queue'])} · Onay bekleyen: {len(state['pending'])} · Hazırlanan: {prepared or '-'}")
    lines.append(f"Bulunan yeni ilan: {found} · Bu çalışmada işlenmesi planlanan: {limit} · Raporlanan: {len(report)}")
    if sources_mod.LAST_STATS:
        lines.append("Kaynaklar: " + " · ".join(f"{k}: {v}" for k, v in sources_mod.LAST_STATS.items()))
    lines += ["", "| Durum | Kaynak | İlan | Neden / kanıt / link |", "|---|---|---|---|"]
    for status, source, title, why in report:
        lines.append(f"| {status} | {source} | {title[:80].replace('|', '/')} | {why[:520].replace('|', '/').replace(chr(10), ' ')} |")
    util.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (util.DATA_DIR / "last_run.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
