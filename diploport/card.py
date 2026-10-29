"""Instagram için 1080x1350 (4:5) JPEG kart. Diploport'un kendi şablonu: üstte stok fotoğraf, solda üstte logo,
altta lacivert gradyan, beyaz başlık, "Ülke | destek" satırı ve en altta site adı.

HTML/CSS şablonu + Playwright (Chromium). Fotoğraf yoksa (ya da ilan kuruma özelse) açık zeminli sade kart + ilan türü simgesi.
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


# Kuruma özel ilanlarda kullanılan sade simgeler (24x24 çizgi simgeleri, lacivert çizgi + altın vurgu)
ICONS = {
    "landmark": '<path d="M3 21h18"/><path d="M5 21V10M9.5 21V10M14.5 21V10M19 21V10"/><path d="M3 10l9-6.5 9 6.5z"/><path d="M2.5 21h19"/>',
    "briefcase": '<rect x="3" y="7" width="18" height="13" rx="2.2"/><path d="M9 7V5.2A1.7 1.7 0 0 1 10.7 3.5h2.6A1.7 1.7 0 0 1 15 5.2V7"/><path d="M3 13h18"/><path d="M10.5 13v2h3v-2"/>',
    "cap": '<path d="M2 9.5l10-5 10 5-10 5z"/><path d="M6 12v4.5c0 1.6 2.7 3 6 3s6-1.4 6-3V12"/><path d="M22 9.5v6"/>',
    "heart": '<path d="M12 20.5s-7.5-4.6-7.5-10.4a4.2 4.2 0 0 1 7.5-2.6 4.2 4.2 0 0 1 7.5 2.6c0 5.8-7.5 10.4-7.5 10.4z"/>',
    "trophy": '<path d="M8 4h8v5.2a4 4 0 0 1-8 0z"/><path d="M8 6H5v1.2A3 3 0 0 0 8 10.2M16 6h3v1.2a3 3 0 0 1-3 3"/><path d="M12 13.2V17M8.5 20.5h7M10 17h4"/>',
    "plane": '<path d="M3 11.2L21 3.5 13.3 21.5l-2.3-7z"/><path d="M11 14.5L21 3.5"/>',
}
CATEGORY_ICONS = {"is": "briefcase", "staj": "briefcase", "burs": "cap", "egitim": "cap", "gonulluluk": "heart",
                  "yarisma": "trophy", "hibe": "plane", "diger": "plane"}


def icon_for(post: dict) -> str:
    name = post.get("icon") or CATEGORY_ICONS.get(post.get("category", "diger"), "plane")
    return name if name in ICONS else "plane"


def is_institution(post: dict) -> bool:
    return post.get("visual_kind", "institution") != "scene"   # türü bilinmeyen/eski kayıt: güvenli varsayılan, stok foto yok


def _plain_layer(post: dict, logo: bytes | None = None) -> str:
    from .photo import data_uri

    icon = ICONS[icon_for(post)]
    orbit = ('<svg class="orbit" viewBox="0 0 1080 760" fill="none">'
             '<ellipse cx="540" cy="370" rx="330" ry="330" stroke="#1E2C55" stroke-opacity=".10" stroke-width="2"/>'
             '<ellipse cx="540" cy="370" rx="440" ry="440" stroke="#1E2C55" stroke-opacity=".06" stroke-width="2"/>'
             '<circle cx="872" cy="262" r="9" fill="#E0A526"/><circle cx="186" cy="520" r="7" fill="#E0A526" fill-opacity=".8"/>'
             '<circle cx="808" cy="590" r="5" fill="#1E2C55" fill-opacity=".35"/></svg>')
    if logo:   # kurumun logosu: beyaz panel içinde, oranı korunarak
        centre = f'<div class="panel"><img alt="" src="{data_uri(logo)}"></div>'
    else:
        svg = (f'<svg viewBox="0 0 24 24" fill="none" stroke="#1E2C55" stroke-width="1.35" stroke-linecap="round" '
               f'stroke-linejoin="round">{icon}</svg>')
        centre = f'<div class="badge">{svg}</div>'
    return f'<div class="plain"><div class="dots"></div>{orbit}{centre}</div>'


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


def build_html(post: dict, cfg: dict, photo: bytes | None = None, logo: bytes | None = None) -> str:
    from .photo import data_uri

    tpl = (ROOT / "templates" / "card.html").read_text(encoding="utf-8")
    title = post["title_tr"].strip()
    photo_style = f"background-image: url('{data_uri(photo)}');" if photo else ""
    values = {
        "fonts_css": _fonts_css(),
        "gradient": GRADIENT,
        "photo_style": photo_style,
        "plain_layer": "" if photo else _plain_layer(post, logo),
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


def render_card(post: dict, cfg: dict, out_path: Path, today: date | None = None, photo: bytes | None = None,
                logo: bytes | None = None) -> Path:
    """HTML'i JPEG'e çevirir. `photo` verilmezse Unsplash/Pexels'ten aranır; bulunamazsa fotoğrafsız üretilir.
    Kullanılan fotoğrafın künyesi post["photo_credit"/"photo_url"/"photo_query"] alanlarına yazılır.
    Playwright yoksa/başarısızsa istisna fırlatır (üst katman yakalar)."""
    from playwright.sync_api import sync_playwright

    from .orgvisual import find_org_visual
    from .photo import find_background

    if photo is None and logo is None:
        if is_institution(post):
            # Kuruma özel ilan: önce kurumun kendi görseli (Vikipedi logosu / kurum fotoğrafı), sonra sektöre uygun stok fotoğraf
            ov = find_org_visual(post.get("organization", ""), cfg)
            if ov and ov["kind"] == "logo":
                logo = ov["bytes"]
                post["photo_query"], post["photo_url"] = f"logo: {ov['title']} ({ov['lang']}.wikipedia)", ov["url"]
            elif ov:
                photo = ov["bytes"]
                post["photo_credit"], post["photo_url"] = ov["credit"], ov["url"]
                post["photo_query"] = f"wiki foto: {ov['title']} ({ov['lang']}.wikipedia)"
        if photo is None and logo is None:
            bg = find_background(post, cfg)
            if bg:
                photo = bg["bytes"]
                post["photo_credit"], post["photo_url"], post["photo_query"] = bg["credit"], bg["url"], bg["query"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = build_html(post, cfg, photo, logo)
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
