"""Kariyer Kapısı (kariyerkapisi.gov.tr) ilanları.

Sitenin resmî RSS'i (https://kariyerkapisi.gov.tr/RSS) sadece başlık + bağlantı verir; ilan metni sayfada JavaScript ile
sitenin herkese açık API'sinden yüklenir. Buradaki fetch_article() aynı API'yi çağırıp metni Gemini'ye uygun hâle getirir.
Başvuru linki olarak ilanın kendi Kariyer Kapısı sayfası kullanılır (başvuru zaten e-Devlet ile orada yapılır).
"""
from __future__ import annotations

import html
import logging
import re
from urllib.parse import parse_qs, urlsplit

import requests

from .article import Article, shorten
from .util import USER_AGENT

log = logging.getLogger("diploport.kariyerkapisi")

API = "https://api.kariyerkapisi.gov.tr/api/ilan/GetIlanPreviewPublic"
SITE = "https://kariyerkapisi.gov.tr"

_BB_BREAK = re.compile(r"\[(?:br|/p|/li|/tr|/h\d|\*)\s*/?\]|\[/?(?:list|table)\]", re.I)
_BB_TAG = re.compile(r"\[/?[a-zA-Z*][a-zA-Z0-9]*(?:=[^\]]*)?\]")
_HTML_TAG = re.compile(r"<[^>]+>")


def guid_of(link: str) -> str:
    vals = parse_qs(urlsplit(link).query).get("i") or []
    return vals[0] if vals else ""


def bbcode_to_text(s: str) -> str:
    """[b]..[/b], [size=14pt] vb. işaretlerini ve HTML'i atar; paragraf kırılımlarını satıra çevirir."""
    s = html.unescape(s or "")
    s = _BB_BREAK.sub("\n", s)
    s = re.sub(r"</?(?:p|br|li|tr|div|h\d)\b[^>]*>", "\n", s, flags=re.I)
    s = _BB_TAG.sub("", s)
    s = _HTML_TAG.sub("", s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def _day(ts: str) -> str:
    return (ts or "")[:10]


def to_text(d: dict, max_chars: int) -> str:
    head = [f"Kurum: {d.get('kurumAdi') or '-'}" + (f" / {d['birimAdi']}" if d.get("birimAdi") else ""),
            f"İlan türü: {d.get('ilanTuru') or '-'}", f"İlan numarası: {d.get('ilN_NO') or '-'}"]
    if d.get("basTarih"):
        head.append(f"Başvuru başlangıç tarihi: {_day(d['basTarih'])}")
    if d.get("bitTarih"):
        head.append(f"Son başvuru tarihi: {_day(d['bitTarih'])}")
    if d.get("kontenjan"):
        head.append(f"Kontenjan: {d['kontenjan']}")
    head.append("Başvuru Kariyer Kapısı üzerinden (e-Devlet girişiyle) yapılır; ilan Türkiye Cumhuriyeti vatandaşlarına yöneliktir.")
    body = bbcode_to_text(d.get("ilanMetni") or "")
    return shorten("\n".join(head) + "\n\n" + body, max_chars)


_API_UNREACHABLE = False   # API (api.kariyerkapisi.gov.tr) yurt dışından bağlantıyı zaman aşımına uğratabilir; bir kez denenir


def fallback_text(item: dict) -> str:
    """API'ye ulaşılamazsa RSS'teki künyeyle yetinilir; modele hiçbir ayrıntı uydurmaması söylenir."""
    lines = [f"İlan başlığı: {item.get('title', '')}"]
    if item.get("category"):
        lines.append(f"İlan türü: {item['category']}")
    if item.get("published"):
        lines.append(f"Yayın/başvuru başlangıç tarihi: {item['published'][:10]}")
    lines.append("Not: Ayrıntılı ilan metni alınamadı; başvuru şartları ve son başvuru tarihi Kariyer Kapısı ilan sayfasındadır.")
    lines.append("Başvuru Kariyer Kapısı üzerinden (e-Devlet girişiyle) yapılır; ilan Türkiye Cumhuriyeti vatandaşlarına yöneliktir.")
    return "\n".join(lines)


def fetch_article(item: dict, cfg: dict) -> Article:
    global _API_UNREACHABLE
    link, title = item["link"], item.get("title", "")
    guid = guid_of(link)
    if not guid:
        raise ValueError("ilan kimliği yok")
    if _API_UNREACHABLE:
        return Article(url=link, final_url=link, title=title, text=fallback_text(item), links=[], degraded=True)
    try:
        r = requests.post(
            API, json={"ilanGuid": guid}, timeout=(8, 20),
            headers={"User-Agent": USER_AGENT, "Origin": SITE, "Referer": SITE + "/", "Accept": "application/json"},
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        _API_UNREACHABLE = True
        log.warning("Kariyer Kapısı API'sine ulaşılamadı (%s); ilanlar yalnızca RSS künyesiyle işlenecek", type(e).__name__)
        return Article(url=link, final_url=link, title=title, text=fallback_text(item), links=[], degraded=True)
    if r.status_code == 204:
        raise ValueError("ilan artık yayında değil")
    r.raise_for_status()
    d = r.json()
    art_title = (d.get("ilanBaslik") or title or "").strip()
    return Article(url=link, final_url=link, title=art_title,
                   text=to_text(d, cfg["gemini"]["max_article_chars"]), links=[])
