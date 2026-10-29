"""Kartın arkasına otomatik stok fotoğraf. Sağlayıcılar: Unsplash ve Pexels (ikisi de ücretsiz anahtar, ticari kullanım serbest).

Akış: Gemini ilan için 3 İngilizce arama ifadesi üretir (en özelden en genele) -> sırayla aranır ->
yeterince büyük, çok karanlık olmayan dikey fotoğraflardan biri seçilir -> 1080x1350 kırpılmış hâli indirilir.
Anahtarı olan ilk sağlayıcı kullanılır (config.yaml > photo.providers sırası); o sonuç vermezse sıradakine geçilir.
Hiçbiri bulunamazsa ya da hiç anahtar yoksa None döner; kart şablonun düz (beyaz zeminli) hâliyle üretilir,
hat asla durmaz.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

import requests

from .util import USER_AGENT

log = logging.getLogger("diploport.photo")

PEXELS_API = "https://api.pexels.com/v1/search"
UNSPLASH_API = "https://api.unsplash.com/search/photos"
KEY_ENV = {"unsplash": "UNSPLASH_ACCESS_KEY", "pexels": "PEXELS_API_KEY"}
DEFAULT_PROVIDERS = ["unsplash", "pexels"]
W, H = 1080, 1350


class PhotoError(RuntimeError):
    pass


def luma(hex_color: str) -> float:
    """Sağlayıcının verdiği ortalama/baskın renkten 0-1 arası parlaklık (logo koyu zeminde okunmaz)."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return 0.5
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 0.5
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _with_params(url: str, params: str) -> str:
    return url + ("&" if "?" in url else "?") + params


# Her sağlayıcı aynı biçime çevrilir:
#   {"id","width","height","color","urls":[indirme adayları, en iyisi önce],"credit","url","track","provider"}

def normalize_pexels(p: dict) -> dict:
    src = p.get("src") or {}
    orig = src.get("original")
    urls = []
    if orig:
        urls.append(_with_params(orig, f"auto=compress&cs=tinysrgb&fit=crop&w={W}&h={H}"))  # tam 1080x1350 kırpım
    urls += [u for u in (src.get("portrait"), src.get("large2x")) if u]
    return {"id": p.get("id"), "width": p.get("width") or 0, "height": p.get("height") or 0,
            "color": p.get("avg_color", ""), "urls": urls if orig else [],
            "credit": f"{p.get('photographer') or 'Pexels'} / Pexels", "url": p.get("url", ""),
            "track": None, "provider": "pexels"}


def normalize_unsplash(p: dict) -> dict:
    urls_ = p.get("urls") or {}
    raw = urls_.get("raw")
    urls = []
    if raw:
        urls.append(_with_params(raw, f"w={W}&h={H}&fit=crop&crop=entropy&q=80&fm=jpg"))
    urls += [u for u in (urls_.get("regular"), urls_.get("full")) if u]
    user = p.get("user") or {}
    page = (p.get("links") or {}).get("html", "")
    return {"id": p.get("id"), "width": p.get("width") or 0, "height": p.get("height") or 0,
            "color": p.get("color", ""), "urls": urls if raw else [],
            "credit": f"{user.get('name') or 'Unsplash'} / Unsplash",
            "url": _with_params(page, "utm_source=diploport&utm_medium=referral") if page else "",
            "track": (p.get("links") or {}).get("download_location"), "provider": "unsplash"}


def build_queries(post: dict, cfg: dict) -> list[str]:
    """Modelin sorguları + kategoriye göre genel yedek sorgu (tekrarsız, sıralı)."""
    out: list[str] = []
    for q in list(post.get("image_queries") or [])[:3]:
        if isinstance(q, str) and q.strip():
            out.append(q.strip()[:80])
    fb = (cfg.get("photo") or {}).get("fallback_queries") or {}
    generic = fb.get(post.get("category")) or fb.get("diger")
    if generic:
        out.append(generic)
    seen, uniq = set(), []
    for q in out:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return uniq


def usable(photos: list[dict], cfg: dict) -> list[dict]:
    """Yeterince büyük (kırpınca büyütme olmasın) ve çok karanlık olmayan fotoğraflar. Sıra korunur (alaka)."""
    min_luma = float((cfg.get("photo") or {}).get("min_luma", 0.30))
    good = []
    for p in photos:
        if (p.get("width") or 0) < W or (p.get("height") or 0) < H:
            continue
        if luma(p.get("color", "")) < min_luma:
            continue
        if not p.get("urls"):
            continue
        good.append(p)
    return good


def pick(photos: list[dict], post_id: str, pick_from: int = 8) -> dict | None:
    """İlk N uygun sonuçtan, ilan kimliğine göre sabit biri: aynı sorguda her ilan aynı fotoğrafı almasın."""
    pool = photos[:pick_from]
    if not pool:
        return None
    n = int(hashlib.sha1(post_id.encode("utf-8")).hexdigest()[:8], 16)
    return pool[n % len(pool)]


def search_pexels(query: str, key: str, per_page: int = 20) -> list[dict]:
    r = requests.get(
        PEXELS_API,
        params={"query": query, "orientation": "portrait", "size": "large", "per_page": per_page, "locale": "en-US"},
        headers={"Authorization": key, "User-Agent": USER_AGENT},
        timeout=30,
    )
    if r.status_code != 200:
        raise PhotoError(f"Pexels HTTP {r.status_code}: {r.text[:120]}")
    return [normalize_pexels(p) for p in r.json().get("photos", [])]


def search_unsplash(query: str, key: str, per_page: int = 20) -> list[dict]:
    r = requests.get(
        UNSPLASH_API,
        params={"query": query, "orientation": "portrait", "per_page": per_page, "content_filter": "high"},
        headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1", "User-Agent": USER_AGENT},
        timeout=30,
    )
    if r.status_code != 200:
        raise PhotoError(f"Unsplash HTTP {r.status_code}: {r.text[:120]}")
    return [normalize_unsplash(p) for p in r.json().get("results", [])]


SEARCHERS = {"unsplash": search_unsplash, "pexels": search_pexels}


def download(photo: dict) -> bytes:
    last = ""
    for url in photo.get("urls") or []:
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        except requests.RequestException as e:
            last = str(e)
            continue
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and ctype.startswith("image/") and 20_000 < len(r.content) < 12_000_000:
            return r.content
        last = f"HTTP {r.status_code} {ctype} {len(r.content)}B"
    raise PhotoError(f"fotoğraf indirilemedi: {last}")


def _track_unsplash_download(photo: dict, key: str) -> None:
    """Unsplash kuralı: fotoğraf kullanıldığında 'download' uç noktası çağrılır (sonuç önemsiz, hata yutulur)."""
    if photo.get("provider") != "unsplash" or not photo.get("track"):
        return
    try:
        requests.get(photo["track"], headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1",
                                              "User-Agent": USER_AGENT}, timeout=15)
    except requests.RequestException as e:
        log.info("Unsplash indirme bildirimi gönderilemedi: %s", e)


def _providers(cfg: dict) -> list[tuple[str, str]]:
    """Anahtarı tanımlı sağlayıcılar, config sırasıyla: [(ad, anahtar), ...]"""
    names = (cfg.get("photo") or {}).get("providers") or DEFAULT_PROVIDERS
    out = []
    for n in names:
        key = os.environ.get(KEY_ENV.get(n, ""), "").strip() if n in KEY_ENV else ""
        if key:
            out.append((n, key))
    return out


def find_background(post: dict, cfg: dict) -> dict | None:
    """Dönüş: {"bytes", "credit", "url", "query", "id", "provider"} ya da None (anahtar yok / sonuç yok / hata)."""
    if not (cfg.get("photo") or {}).get("enabled", True):
        return None
    providers = _providers(cfg)
    if not providers:
        log.info("Fotoğraf sağlayıcı anahtarı yok (UNSPLASH_ACCESS_KEY / PEXELS_API_KEY): kart fotoğrafsız üretilecek")
        return None
    pick_from = int((cfg.get("photo") or {}).get("pick_from", 8))
    queries = build_queries(post, cfg)
    for name, key in providers:
        for q in queries:
            try:
                photos = usable(SEARCHERS[name](q, key), cfg)
                chosen = pick(photos, str(post.get("id", "")), pick_from)
                if not chosen:
                    log.info("%s: sonuç yok/uygun değil: %r", name, q)
                    continue
                data = download(chosen)
            except (PhotoError, requests.RequestException, ValueError, KeyError) as e:
                log.warning("%s: fotoğraf aranamadı (%r): %s", name, q, e)
                if isinstance(e, PhotoError) and any(c in str(e) for c in ("HTTP 401", "HTTP 403", "HTTP 429")):
                    break   # anahtar geçersiz ya da kota dolu: bu sağlayıcıda diğer sorguları deneme
                continue
            _track_unsplash_download(chosen, key)
            return {"bytes": data, "credit": chosen["credit"], "url": chosen["url"], "query": q,
                    "id": chosen["id"], "provider": name}
    return None


def data_uri(img: bytes) -> str:
    mime = "image/png" if img[:4] == b"\x89PNG" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(img).decode()
