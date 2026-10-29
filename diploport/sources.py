"""RSS kaynaklarını doğrudan çeker (aracı birleştirme servisi yok), yeni olanları seçer."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from .util import USER_AGENT, canonical_url, now_utc, short_hash

log = logging.getLogger("diploport.sources")


def _entry_date(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return now_utc()  # tarihsiz kayıt: ilk görüldüğü an. Tekrar paylaşımı "seen" engeller.


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
FEED_ERRORS: dict[str, str] = {}   # kaynak adı -> son hata (rapor için)


def _get(url: str, ua: str, timeout: int) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": ua, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
                     timeout=timeout)
    r.raise_for_status()
    return r


def fetch_feed(src: dict, timeout: int = 20) -> list[dict]:
    FEED_ERRORS.pop(src["name"], None)
    try:
        try:
            r = _get(src["url"], USER_AGENT, timeout)
        except requests.HTTPError as e:
            # Bazı siteler botları 403/406 ile reddeder; sıradan bir tarayıcı kimliğiyle bir kez daha dene.
            if e.response is not None and e.response.status_code in (403, 406):
                r = _get(src["url"], BROWSER_UA, timeout)
            else:
                raise
    except requests.RequestException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        FEED_ERRORS[src["name"]] = f"HTTP {code}" if code else type(e).__name__
        log.warning("Kaynak okunamadı (%s): %s", src["name"], e)
        return []
    parsed = feedparser.parse(r.content)
    if not parsed.entries:
        FEED_ERRORS[src["name"]] = "yanıt geldi ama RSS kaydı yok (" + (r.headers.get("content-type", "?").split(";")[0]) + ")"
    items = []
    skip = [x.lower() for x in src.get("skip_categories") or []]
    for e in parsed.entries:
        link = (e.get("link") or "").strip()
        title = (e.get("title") or "").strip()
        if not link or not title:
            continue
        cats = " ".join(t.get("term", "") for t in e.get("tags") or []).lower()
        if skip and any(x in cats for x in skip):   # kaynağın kendi kategorisine göre baştan ele (Gemini'ye gitmez)
            continue
        items.append(
            {
                "id": short_hash(canonical_url(link)),
                "source": src["name"],
                "source_cfg": {
                    "assume_eligible": bool(src.get("assume_eligible")),
                    "link_fallback_to_source": bool(src.get("link_fallback_to_source")),
                    "fetcher": src.get("fetcher", ""),
                },
                "title": title,
                "link": link,
                "published": _entry_date(e).isoformat(),
                "summary": (e.get("summary") or "")[:1500],
            }
        )
    log.info("%s: %d kayıt", src["name"], len(items))
    return items


LAST_STATS: dict[str, str] = {}   # son çağrıda kaynak başına özet (rapor için): "3 yeni / 20 kayıt" ya da "okunamadı"


def collect_new(cfg: dict, state: dict) -> list[dict]:
    """Görülmemiş ve yeterince yeni kayıtları, kaynaklar arasında dönüşümlü sırayla döndürür."""
    max_age = timedelta(days=cfg["limits"]["max_age_days"])
    per_source: list[list[dict]] = []
    LAST_STATS.clear()
    for src in cfg["sources"]:
        entries = fetch_feed(src)
        fresh = [
            it
            for it in entries
            if it["id"] not in state["seen"]
            and it["id"] not in state["pending"]
            and not any(q["id"] == it["id"] for q in state["queue"])
            and now_utc() - datetime.fromisoformat(it["published"]) <= max_age
        ]
        fresh.sort(key=lambda x: x["published"], reverse=True)
        LAST_STATS[src["name"]] = (f"{len(fresh)} yeni / {len(entries)} kayıt" if entries
                                   else f"okunamadı: {FEED_ERRORS.get(src['name'], 'boş')}")
        per_source.append(fresh)
        time.sleep(0.5)

    # Round-robin: tek bir kaynak tüm kotayı yemesin.
    out: list[dict] = []
    seen_ids: set[str] = set()
    i = 0
    while any(per_source):
        for lst in per_source:
            if lst:
                it = lst.pop(0)
                if it["id"] not in seen_ids:
                    seen_ids.add(it["id"])
                    out.append(it)
        i += 1
    return out
