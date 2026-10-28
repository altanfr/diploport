"""Instagram için 1080x1350 (4:5) JPEG kart. Diploport'un kendi şablonu: üstte stok fotoğraf, solda üstte logo,
altta lacivert gradyan, beyaz başlık, "Ülke | destek" satırı ve en altta site adı.

HTML/CSS şablonu + Playwright (Chromium). Fotoğraf yoksa şablonun beyaz zeminli hâli kullanılır.
"""
from __future__ import annotations

import base64
import html
import os
from datetime import date
from functools import lru_cache
from pathlib import Path

from .util import ROOT

WIDTH, HEIGHT = 1080, 1350

# Şablon PNG'sinden ölçülen gradyan (yukarıdan aşağıya): ~%50'ye kadar saydam, sonra lacivert (30,44,85)
GRADIENT = (
    "rgba(30,44,85,0) 50%, rgba(30,44,85,0.104) 52.6%, rgba(30,44,85,0.195) 55.2%, rgba(30,44,85,0.291) 57.8%, "
    "rgba(30,44,85,0.370) 60.4%, rgba(30,44,85,0.454) 63%, rgba(30,44,85,0.525) 65.6%, rgba(30,44,85,0.598) 68.1%, "
    "rgba(30,44,85,0.655) 70.7%, rgba(30,44,85,0.718) 73.3%, rgba(30,44,85,0.766) 75.9%, rgba(30,44,85,0.818) 78.5%, "
    "rgba(30,44,85,0.858) 81.1%, rgba(30,44,85,0.896) 83.7%, rgba(30,44,85,0.924) 86.3%, rgba(30,44,85,0.952) 88.9%, "
    "rgba(30,44,85,0.970) 91.5%, rgba(30,44,85,0.987) 94.1%, rgba(30,44,85,0.995) 96.7%, rgba(30,44,85,1) 99.3%"
)

CATEGORY_LABELS = {
    "burs": "Burs", "staj": "Staj", "is": "İş", "gonulluluk": "Gönüllülük", "egitim": "Eğitim",
    "yarisma": "Yarışma", "hibe": "Hibe", "diger": "Fırsat",
}


def category_label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat, CATEGORY_LABELS["diger"])


@lru_cache(maxsize=1)
def _fonts_css() -> str:
    css = []
    for weight, fname in ((400, "Poppins-Regular.ttf"), (500, "Poppins-Medium.ttf"), (700, "Poppins-Bold.ttf")):
        data = base64.b64encode((ROOT / "assets" / "fonts" / fname).read_bytes()).decode()
        css.append(
            f"@font-face {{ font-family: 'PoppinsD'; font-weight: {weight}; "
            f"src: url(data:font/ttf;base64,{data}) format('truetype'); }}"
        )
    return "\n".join(css)


@lru_cache(maxsize=1)
def _logo_src() -> str:
    data = base64.b64encode((ROOT / "assets" / "logo.png").read_bytes()).decode()
    return "data:image/png;base64," + data


def _title_size(title: str) -> int:
    n = len(title)
    return 72 if n <= 38 else 66 if n <= 58 else 60 if n <= 76 else 54


def subtitle(post: dict) -> str:
    """"Ülke | destek" (ör. "Almanya | Tam burs"). Destek bilinmiyorsa kategori etiketi."""
    country = (post.get("country_tr") or "").strip()
    support = (post.get("funding_tr") or "").strip()
    if not support or support.lower().startswith("belirtilmemi"):
        support = category_label(post.get("category", "diger"))
    return f"{country} | {support}" if country else support


def build_html(post: dict, cfg: dict, photo: bytes | None = None) -> str:
    from .photo import data_uri

    tpl = (ROOT / "templates" / "card.html").read_text(encoding="utf-8")
    title = post["title_tr"].strip()
    photo_style = f"background-image: url('{data_uri(photo)}');" if photo else ""
    values = {
        "fonts_css": _fonts_css(),
        "gradient": GRADIENT,
        "photo_style": photo_style,
        "logo_src": _logo_src(),
        "title": html.escape(title),
        "title_size": str(_title_size(title)),
        "subtitle": html.escape(subtitle(post)),
        "site_text": html.escape(cfg["brand"].get("footer_text", "DİPLOPORT.COM")),
        "site_size": "34",
        "site_spacing": "0",
    }
    for k, v in values.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def render_card(post: dict, cfg: dict, out_path: Path, today: date | None = None, photo: bytes | None = None) -> Path:
    """HTML'i JPEG'e çevirir. `photo` verilmezse Pexels'ten aranır; bulunamazsa fotoğrafsız üretilir.
    Kullanılan fotoğrafın künyesi post["photo_credit"/"photo_url"/"photo_query"] alanlarına yazılır.
    Playwright yoksa/başarısızsa istisna fırlatır (üst katman yakalar)."""
    from playwright.sync_api import sync_playwright

    from .photo import find_background

    if photo is None:
        bg = find_background(post, cfg)
        if bg:
            photo = bg["bytes"]
            post["photo_credit"], post["photo_url"], post["photo_query"] = bg["credit"], bg["url"], bg["query"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = build_html(post, cfg, photo)
    exe = os.environ.get("CHROMIUM_PATH") or None
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
            page.set_content(doc, wait_until="load")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(out_path), type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
        finally:
            browser.close()
    return out_path
