"""Makale sayfasını açar: temiz metin + sayfadaki GERÇEK harici linkler.

Başvuru linki sorununun çözümü burada: yapay zekâ link yazmaz, buradaki numaralı
aday linklerden birini seçer; sonra validate_link() ile çalıştığı doğrulanır.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .util import USER_AGENT, clean_url, domain_of

log = logging.getLogger("diploport.article")

CONTENT_SELECTORS = [
    "article .entry-content", ".entry-content", ".post-content", ".td-post-content",
    ".single-post-content", "article", "main", "#content", "body",
]
NOISE_TAGS = ["script", "style", "noscript", "nav", "footer", "aside", "form", "iframe", "svg", "header"]
NOISE_CLASSES = re.compile(r"(share|social|related|comment|sidebar|widget|newsletter|breadcrumb|author-box|cookie)", re.I)

APPLY_WORDS = re.compile(
    r"(apply|application|official|website|visit|register|sign up|more info|details|link|click here|start your|"
    r"başvur|resmi|resmî|kayıt|detay|site)", re.I)
APPLY_URL_WORDS = re.compile(r"(apply|application|scholarship|call|programme|program|opportunit|vacanc|career|jobs?|internship|volunteer|burs|basvur)", re.I)
JUNK_LINK = re.compile(r"(/tag/|/category/|/author/|/feed|/wp-content/|/wp-login|/privacy|/terms|/contact|/about)", re.I)


@dataclass
class Article:
    url: str
    final_url: str
    title: str
    text: str
    links: list[dict] = field(default_factory=list)   # {"id","url","text","context","score"}
    degraded: bool = False                            # ayrıntılı metin alınamadı, yalnızca başlık/künye var


def _get(url: str, timeout: int = 20) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,tr;q=0.8"}, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r


def blocked_domains(cfg: dict, extra: str = "") -> set[str]:
    blocked = {d.lower() for d in cfg.get("blocked_link_domains", [])}
    for s in cfg.get("sources", []):
        blocked.add(domain_of(s["url"]))
    if extra:
        blocked.add(domain_of(extra))
    # politepaul sadece bir aktarıcı; başvuru linki olamaz
    blocked.add("politepaul.com")
    return blocked


def shorten(text: str, max_chars: int) -> str:
    """Uygunluk bilgisi genelde sondaki "Eligibility" bölümünde olur: baş + son birlikte tutulur."""
    if len(text) <= max_chars:
        return text
    head, tail = int(max_chars * 0.65), int(max_chars * 0.35)
    return text[:head] + "\n[...]\n" + text[-tail:]


def parse_article(html: str, url: str, cfg: dict, max_chars: int = 7000, max_links: int = 20) -> Article:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")[:200]

    container = None
    for sel in CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node and len(node.get_text(" ", strip=True)) > 400:
            container = node
            break
    container = container or soup.body or soup

    for tag in container.find_all(NOISE_TAGS):
        tag.decompose()
    for tag in container.find_all(class_=NOISE_CLASSES):
        if tag.decomposed:
            continue
        # Elementor sayfalarında ana içerik ve "Apply Now" düğmeleri "elementor-widget-*" sınıflarının içindedir;
        # onlar gürültü değildir. Sadece Elementor'a ait olmayan sınıflara bakılır.
        classes = [c for c in (tag.get("class") or []) if not c.startswith("elementor")]
        if any(NOISE_CLASSES.search(c) for c in classes):
            tag.decompose()

    # Linkler: metne çevirmeden ÖNCE toplanır.
    page_domain = domain_of(url)
    blocked = blocked_domains(cfg, url)
    cands: dict[str, dict] = {}
    for a in container.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        abs_url = clean_url(urljoin(url, href))
        if urlsplit(abs_url).scheme not in ("http", "https"):
            continue
        dom = domain_of(abs_url)
        if dom == page_domain or dom in blocked or JUNK_LINK.search(urlsplit(abs_url).path):
            continue
        text = a.get_text(" ", strip=True)[:120]
        context = (a.parent.get_text(" ", strip=True) if a.parent else "")[:160]
        score = 0
        if APPLY_WORDS.search(text):
            score += 3
        if APPLY_URL_WORDS.search(abs_url):
            score += 1
        if len(urlsplit(abs_url).path.strip("/")) > 0:
            score += 1  # ana sayfa değil, derin link
        if abs_url not in cands or score > cands[abs_url]["score"]:
            cands[abs_url] = {"url": abs_url, "text": text, "context": context, "score": score}

    links = sorted(cands.values(), key=lambda c: -c["score"])[:max_links]
    for i, l in enumerate(links, start=1):
        l["id"] = i

    text = container.get_text("\n", strip=True)
    text = shorten(re.sub(r"\n{2,}", "\n", text), max_chars)
    return Article(url=url, final_url=url, title=title, text=text, links=links)


def fetch_article(url: str, cfg: dict) -> Article:
    r = _get(url)
    art = parse_article(r.text, r.url, cfg, max_chars=cfg["gemini"]["max_article_chars"])
    art.url, art.final_url = url, r.url
    return art


def validate_link(url: str, cfg: dict, timeout: int = 15, allow_domains: set[str] | None = None) -> dict:
    """Linkin çalıştığını ve gerçekten harici/resmî bir adrese çıktığını doğrular.

    Dönüş: {"ok": bool, "url": nihai temiz url, "status": int|None, "soft": bool, "reason": str}
    soft=True: site botlara 403/429 veriyor; link muhtemelen sağlam ama doğrulanamadı.
    """
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True, stream=True)
        status, final = r.status_code, clean_url(r.url)
        r.close()
    except requests.RequestException as e:
        return {"ok": False, "url": url, "status": None, "soft": False, "reason": f"bağlantı hatası: {type(e).__name__}"}

    dom = domain_of(final)
    if dom in blocked_domains(cfg) and dom not in (allow_domains or set()):
        return {"ok": False, "url": final, "status": status, "soft": False, "reason": f"aggregator/sosyal medya alan adı ({dom})"}
    if status < 400:
        return {"ok": True, "url": final, "status": status, "soft": False, "reason": ""}
    if status in (401, 403, 429, 999):
        return {"ok": True, "url": final, "status": status, "soft": True, "reason": f"bot engeli ({status})"}
    return {"ok": False, "url": final, "status": status, "soft": False, "reason": f"HTTP {status}"}
