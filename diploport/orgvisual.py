"""Kuruma/işverene özel ilanlar için kurumun kendi görseli: Vikipedi sayfasının ana görseli (logo ya da bina/kurum fotoğrafı).

Akış: kurum adıyla tr/en Vikipedi'de ara -> sayfa adı kurum adıyla eşleşiyor mu? -> sayfanın ana görseli
  * logo (dosya adında logo/amblem... ya da SVG)            -> kartta beyaz panel içinde gösterilir,
  * büyük bir fotoğraf + serbest lisans (CC BY, CC BY-SA...) -> kartın arka planı olur, yazar/lisans açıklamaya eklenir,
  * başka her şey (küçük fotoğraf, harita, kişi vb.)         -> kullanılmaz.
Bulunamazsa None döner; çağıran ilgili stok fotoğrafa, o da yoksa simge kartına geçer.
"""
from __future__ import annotations

import html
import logging
import re
import unicodedata

import requests

log = logging.getLogger("diploport.orgvisual")

UA = "DiploportBot/1.0 (+https://github.com/altanfr/diploport)"
LANGS = ("tr", "en")
THUMB = 2400
LOGO_HINT = re.compile(r"(logo|amblem|emblem|arma|seal|wordmark|simge|sembol)", re.I)
NOT_VISUAL = re.compile(r"(map|harita|chart|diagram|flag|bayrak|locator|grafik)", re.I)
OK_LICENSE = re.compile(r"^(cc[ -]?by|cc0|public domain|pd\b)", re.I)
GENERIC = {"genel", "mudurlugu", "baskanligi", "ajansi", "kurumu", "universitesi", "rektorlugu", "bakanligi", "fonu",
           "university", "agency", "foundation", "ministry", "institute", "the", "of", "ve", "and", "for", "company",
           "inc", "ltd", "as", "sti", "a", "s"}


def fold(s: str) -> list[str]:
    """Türkçe/aksan duyarsız küçük harfli kelimeler: 'İHALE' -> 'ihale', 'Yardımcılığı' -> 'yardimciligi'."""
    s = unicodedata.normalize("NFKD", (s or "").replace("İ", "I").replace("ı", "i"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).split()


def name_matches(org: str, title: str, rank: int, extract: str = "") -> bool:
    o, t = set(fold(org)), set(fold(title))
    if not o or not t:
        return False
    common = o & t
    if common and len(common) / len(o) >= 0.75:
        return True                                   # kurum adının çoğu sayfa adında geçiyor
    if common and t <= o and (t - GENERIC):
        return True                                   # sayfa adı, kurum adının kısa hâli (Oyak-Renault / Oyak Renault Otomobil...)
    # sayfa adı farklı ama (ör. UNHCR -> "Birleşmiş Milletler Mülteciler Yüksek Komiserliği"): ilk sonuç ve kısaltma girişte geçiyor
    return rank == 1 and " ".join(fold(org)) in " ".join(fold(extract))


def classify(filename: str, w: int, h: int) -> str | None:
    if NOT_VISUAL.search(filename):
        return None
    if LOGO_HINT.search(filename) or filename.lower().endswith(".svg"):
        return "logo"
    if w >= 1080 and h >= 1100:
        return "photo"
    return None


def _api(lang: str, params: dict) -> dict:
    r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params={"format": "json", **params},
                     headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    return r.json()


def search_pages(lang: str, org: str) -> list[dict]:
    j = _api(lang, {"action": "query", "generator": "search", "gsrsearch": org, "gsrlimit": 3, "gsrnamespace": 0,
                    "prop": "pageimages|extracts", "piprop": "original|name|thumbnail", "pithumbsize": THUMB,
                    "pilimit": 3, "exintro": 1, "explaintext": 1, "exchars": 400, "exlimit": 3})
    return sorted(((j.get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 99))


def file_license(lang: str, filename: str) -> dict:
    j = _api(lang, {"action": "query", "titles": "File:" + filename, "prop": "imageinfo", "iiprop": "extmetadata",
                    "iiextmetadatafilter": "Artist|LicenseShortName|NonFree"})
    page = next(iter(((j.get("query") or {}).get("pages") or {}).values()), {})
    em = ((page.get("imageinfo") or [{}])[0]).get("extmetadata") or {}
    val = lambda k: (em.get(k) or {}).get("value", "")  # noqa: E731
    artist = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", val("Artist")))).strip()
    return {"artist": artist, "license": val("LicenseShortName").strip(), "nonfree": str(val("NonFree")).lower() in ("true", "1", "yes")}


def _download(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("image/") or not (2_000 < len(r.content) < 12_000_000):
        raise ValueError(f"görsel indirilemedi: HTTP {r.status_code} {ctype} {len(r.content)}B")
    return r.content


def too_light(data: bytes) -> bool:
    """Beyaz panelde kaybolacak (beyaz yazılı/çok açık) logo mu? Saydam piksellere bakılmaz."""
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(data)).convert("RGBA")
        im.thumbnail((120, 120))
        px = [p for p in im.getdata() if p[3] > 40]
        if not px:
            return True
        lum = sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b, _ in px) / (255 * len(px))
        return lum > 0.88
    except Exception:  # noqa: BLE001 - Pillow yoksa/bozuk dosya: kontrol atlanır
        return False


def find_org_visual(org: str, cfg: dict) -> dict | None:
    """Dönüş: {"kind": "logo"|"photo", "bytes", "credit", "url", "title", "lang"} ya da None."""
    if not (org or "").strip() or not (cfg.get("org_visual") or {}).get("enabled", True):
        return None
    for lang in LANGS:
        try:
            pages = search_pages(lang, org)
        except (requests.RequestException, ValueError) as e:
            log.warning("Vikipedi (%s) aranamadı (%r): %s", lang, org, e)
            continue
        for p in pages:
            fname, thumb, orig = p.get("pageimage"), (p.get("thumbnail") or {}).get("source"), p.get("original") or {}
            if not fname or not thumb or not name_matches(org, p.get("title", ""), p.get("index", 99), p.get("extract", "")):
                continue
            kind = classify(fname, orig.get("width", 0), orig.get("height", 0))
            if not kind:
                continue
            credit = ""
            try:
                if kind == "photo":
                    lic = file_license(lang, fname)
                    if lic["nonfree"] or not OK_LICENSE.match(lic["license"]):
                        log.info("Vikipedi fotoğrafı lisansı uygun değil (%s): %s", fname, lic["license"])
                        continue
                    credit = " / ".join(x for x in (lic["artist"][:60], f"Wikimedia Commons ({lic['license']})") if x)
                data = _download(thumb)
            except (requests.RequestException, ValueError) as e:
                log.warning("Vikipedi görseli alınamadı (%s): %s", fname, e)
                continue
            if kind == "logo" and too_light(data):
                log.info("Logo beyaz panelde okunmaz (çok açık): %s", fname)
                continue
            return {"kind": kind, "bytes": data, "credit": credit, "title": p.get("title", ""), "lang": lang,
                    "url": f"https://{lang}.wikipedia.org/wiki/" + p.get("title", "").replace(" ", "_")}
    return None
