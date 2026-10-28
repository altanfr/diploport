"""Kartın arkasına otomatik stok fotoğraf: Pexels API (ücretsiz anahtar, ticari kullanım serbest).

Akış: Gemini ilan için 3 İngilizce arama ifadesi üretir (en özelden en genele) -> sırayla Pexels'te aranır ->
yeterince büyük, çok karanlık olmayan dikey fotoğraflardan biri seçilir -> 1080x1350 kırpılmış hâli indirilir.
Hiçbiri bulunamazsa ya da anahtar yoksa None döner; kart şablonun düz (beyaz zeminli) hâliyle üretilir,
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

API = "https://api.pexels.com/v1/search"
W, H = 1080, 1350


class PhotoError(RuntimeError):
    pass


def luma(hex_color: str) -> float:
    """Pexels'in 'avg_color' değerinden 0-1 arası parlaklık (logo koyu zeminde okunmaz)."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return 0.5
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 0.5
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


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
        if luma(p.get("avg_color", "")) < min_luma:
            continue
        if not (p.get("src") or {}).get("original"):
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


def search(query: str, key: str, per_page: int = 20) -> list[dict]:
    r = requests.get(
        API,
        params={"query": query, "orientation": "portrait", "size": "large", "per_page": per_page, "locale": "en-US"},
        headers={"Authorization": key, "User-Agent": USER_AGENT},
        timeout=30,
    )
    if r.status_code != 200:
        raise PhotoError(f"Pexels HTTP {r.status_code}: {r.text[:120]}")
    return r.json().get("photos", [])


def download(photo: dict) -> bytes:
    src = photo["src"]
    candidates = [
        f"{src['original']}?auto=compress&cs=tinysrgb&fit=crop&w={W}&h={H}",  # tam 1080x1350 kırpım
        src.get("portrait"),
        src.get("large2x"),
    ]
    last = ""
    for url in [c for c in candidates if c]:
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


def find_background(post: dict, cfg: dict) -> dict | None:
    """Dönüş: {"bytes", "credit", "url", "query", "id"} ya da None (anahtar yok / sonuç yok / hata)."""
    if not (cfg.get("photo") or {}).get("enabled", True):
        return None
    key = os.environ.get("PEXELS_API_KEY", "")
    if not key:
        log.info("PEXELS_API_KEY yok: kart fotoğrafsız (şablonun düz hâli) üretilecek")
        return None
    pick_from = int((cfg.get("photo") or {}).get("pick_from", 8))
    for q in build_queries(post, cfg):
        try:
            photos = usable(search(q, key), cfg)
            chosen = pick(photos, str(post.get("id", "")), pick_from)
            if not chosen:
                log.info("Pexels sonuç yok/uygun değil: %r", q)
                continue
            data = download(chosen)
        except (PhotoError, requests.RequestException, ValueError, KeyError) as e:
            log.warning("Fotoğraf aranamadı (%r): %s", q, e)
            continue
        return {
            "bytes": data,
            "credit": f"{chosen.get('photographer') or 'Pexels'} / Pexels",
            "url": chosen.get("url", ""),
            "query": q,
            "id": chosen.get("id"),
        }
    return None


def data_uri(img: bytes) -> str:
    mime = "image/png" if img[:4] == b"\x89PNG" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(img).decode()
