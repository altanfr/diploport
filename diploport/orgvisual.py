"""Kuruma/işverene özel ilanlar için kurumun kendi görseli. ÖNCELİK: LOGO. Stok fotoğraf ya da şehir görseli asla kullanılmaz.

Logo şu sırayla aranır, ilk bulunan kullanılır (her adım TRACE listesine yazılır; `phototest` raporunda görünür):
  1. elle eklenen logo          assets/org_logos/<kurum-adı>.png|jpg|svg   (otomatik bulunamayan kurumlar için yedek)
  2. ilanın kaynak sayfası      Youthall ilan sayfasındaki şirket logosu
  3. Vikipedi (tr, en)          kurum sayfasının ana görseli ya da sayfadaki "logo" dosyası
  4. Wikidata                   "logo" özelliği (P154); ayrıca resmî web sitesi (P856)
  5. kurumun kendi web sitesi   JSON-LD logo / sayfadaki logo görseli / apple-touch-icon / favicon
        site adresi: modelin önerdiği alan adı, Wikidata, ilanın başvuru linki, kaynak sayfa, kurum adından türetilen tahminler.
        Her site KODLA doğrulanır: sayfa başlığında/site adında kurum adı geçmiyorsa o siteden logo alınmaz.
  6. (logo hiç yoksa) Vikipedi'deki kurum/bina fotoğrafı — yalnızca serbest lisanslı (CC BY, CC0, PD); yazar kartın açıklamasına eklenir.
Hiçbiri bulunamazsa None döner; çağıran ilanı yayımlamaz (kart yapmaz) ve haber verir.
"""
from __future__ import annotations

import html
import io
import json
import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .util import ROOT, domain_of

log = logging.getLogger("diploport.orgvisual")

UA = "DiploportBot/1.0 (+https://github.com/altanfr/diploport)"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
LANGS = ("tr", "en")
THUMB = 2400
ORG_LOGO_DIR = ROOT / "assets" / "org_logos"
LOGO_HINT = re.compile(r"(logo|amblem|emblem|arma|seal|wordmark|simge|sembol)", re.I)
NOT_VISUAL = re.compile(r"(map|harita|chart|diagram|flag|bayrak|locator|grafik)", re.I)
WIKI_BOILER = re.compile(r"(commons-logo|wikimedia|wikiquote|wikisource|wikidata|wiktionary|oojs|icon|question_book|"
                         r"padlock|ambox|edit-clear|symbol_|crystal|nuvola)", re.I)
OK_LICENSE = re.compile(r"^(cc[ -]?by|cc0|public domain|pd\b)", re.I)
GENERIC = {"genel", "mudurlugu", "baskanligi", "ajansi", "kurumu", "universitesi", "rektorlugu", "bakanligi", "fonu",
           "university", "agency", "foundation", "ministry", "institute", "the", "of", "ve", "and", "for", "company",
           "inc", "ltd", "as", "sti", "a", "s", "group", "holding", "grup", "ve"}
SOFT = {"turkiye", "turkey", "people", "campus", "kariyer", "career", "careers", "international", "global", "sirketi",
        "sanayi", "ticaret", "anonim", "limited", "kampus", "tr", "com"}      # site doğrulamada zorunlu sayılmayan kelimeler
BAD_WIKIDATA = re.compile(r"(asteroi|ship built|gemi|hadis|ambassador|elçisi|elcisi|album|albüm|film|song|şarkı|surname|"
                          r"given name|family name|soyadı|first name|village|köy|person|insan)", re.I)
BAD_LOGO_CTX = re.compile(r"(partner|client|sponsor|customer|referans|ortak|footer|award|social|payment|certif|banner|"
                          r"slider|carousel|testimonial|brands)", re.I)
PARKING = re.compile(r"(domain\s*(is\s*)?(for\s*sale|parking|parked)|satılık\s*(alan\s*adı|domain)|alan\s*adı\s*satılık|buy\s+this\s+domain|"
                     r"this\s+domain\s+(is|may\s+be)|sedo\.com|dan\.com|godaddy|domain\s*parking|under\s*construction)", re.I)
SKIP_HOSTS = ("youthall.com", "youthtech.co", "google.", "apple.com", "facebook.com", "instagram.com", "linkedin.com",
              "twitter.com", "x.com", "youtube.com", "youtu.be", "t.me", "wa.me", "whatsapp", "tiktok.com", "pinterest.com")

TRACE: list[str] = []          # son aramanın adım adım günlüğü (phototest raporu için)


def _t(msg: str) -> None:
    TRACE.append(msg)
    del TRACE[:-200]
    log.info("logo: %s", msg)


def fold(s: str) -> list[str]:
    """Türkçe/aksan duyarsız küçük harfli kelimeler: 'İHALE' -> 'ihale', 'Yardımcılığı' -> 'yardimciligi'."""
    s = unicodedata.normalize("NFKD", (s or "").replace("İ", "I").replace("ı", "i"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).split()


def slug(org: str) -> str:
    return "-".join(fold(org))


def name_matches(org: str, title: str, rank: int, extract: str = "", strong: bool = False) -> bool:
    """strong=True: yalnızca birebir ya da kurum adının çoğunun (>=%75) başlıkta geçtiği eşleşmeler (başlığın kurum adının kısa
    hâli olması ya da kısaltma kuralı sayılmaz). Elde zaten doğru bir kaynak logosu varken onu değiştirmek için kullanılır."""
    o, t = set(fold(org)), set(fold(title))
    if not o or not t:
        return False
    if o == t:
        return True
    common = o & t
    if common and len(common) / len(o) >= 0.75:
        return True                                   # kurum adının çoğu sayfa adında geçiyor
    if strong:
        return False
    if common and t <= o and (t - GENERIC):
        return True                                   # sayfa adı, kurum adının kısa hâli (Oyak-Renault / Oyak Renault Otomobil...)
    # sayfa adı farklı ama (ör. UNHCR -> "Birleşmiş Milletler Mülteciler Yüksek Komiserliği"): ilk sonuç ve
    #   (kısaltma girişte geçiyor YA DA kurum adı BÜYÜK HARFLİ tek bir kısaltma: UNHCR, UNICEF, DAAD...)
    if rank != 1:
        return False
    org = org.strip()
    return " ".join(fold(org)) in " ".join(fold(extract)) or (len(o) == 1 and org.isupper() and len(org) >= 4)


def classify(filename: str, w: int, h: int) -> str | None:
    if NOT_VISUAL.search(filename):
        return None
    if LOGO_HINT.search(filename) or filename.lower().endswith(".svg"):
        return "logo"
    if w >= 1080 and h >= 1100:
        return "photo"
    return None


# --- görsel doğrulama / boyut / koyu-açık ---------------------------------------------------

def is_svg(data: bytes) -> bool:
    return b"<svg" in data[:3000].lower()


def _autocrop(im):
    """Logonun etrafındaki düz (beyaz/saydam) boşluğu kırpar: küçük logo panelde daha büyük görünür."""
    from PIL import Image, ImageChops

    rgba = im.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    if all(c[3] < 20 for c in corners):
        bbox = rgba.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
    else:
        ref = corners[0]
        if any(sum(abs(a - b) for a, b in zip(ref[:3], c[:3])) > 60 for c in corners):
            return im                                          # zemin tek renk değil: kırpma
        diff = ImageChops.difference(rgba.convert("RGB"), Image.new("RGB", rgba.size, ref[:3])).convert("L")
        bbox = diff.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not bbox:
        return im
    pad = max(4, round(0.06 * max(bbox[2] - bbox[0], bbox[3] - bbox[1])))
    box = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(w, bbox[2] + pad), min(h, bbox[3] + pad))
    if (box[2] - box[0]) * (box[3] - box[1]) >= 0.85 * w * h or min(box[2] - box[0], box[3] - box[1]) < 16:
        return im
    return im.crop(box)


def norm_image(data: bytes, min_side: int = 0) -> bytes | None:
    """Kullanılabilir bir logo görseli mi? PNG/JPEG/SVG olarak döner (diğer biçimler PNG'ye çevrilir), değilse None."""
    if not data or len(data) > 8_000_000:
        return None
    if is_svg(data):
        return data if len(data) <= 700_000 else None
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001 - bozuk dosya / HTML sayfası
        return None
    w, h = im.size
    if min(w, h) < max(min_side, 16) or max(w, h) / max(1, min(w, h)) > 8:
        return None
    cropped = _autocrop(im)
    if cropped is im and (data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff"):
        return data
    out = io.BytesIO()
    cropped.convert("RGBA").save(out, "PNG")
    return out.getvalue()


def low_res(data: bytes) -> bool:
    """Küçük raster logo (büyütülünce bulanık): daha iyi bir kaynak varsa o tercih edilir."""
    if is_svg(data):
        return False
    w, h = image_size(data)
    return max(w, h) < 300


def image_size(data: bytes) -> tuple[float, float]:
    """Oranı korumak için görselin (genişlik, yükseklik) değeri. SVG'de viewBox/width/height'a bakılır."""
    if is_svg(data):
        head = data[:3000].decode("utf-8", "ignore")
        m = re.search(r'viewBox\s*=\s*["\']\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)', head)
        if m and float(m.group(1)) > 0 and float(m.group(2)) > 0:
            return float(m.group(1)), float(m.group(2))
        w, h = (re.search(rf'<svg[^>]*\s{k}\s*=\s*["\']([\d.]+)', head) for k in ("width", "height"))
        if w and h and float(w.group(1)) > 0 and float(h.group(1)) > 0:
            return float(w.group(1)), float(h.group(1))
        return 300.0, 300.0
    try:
        from PIL import Image

        return tuple(float(x) for x in Image.open(io.BytesIO(data)).size)  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
        return 300.0, 300.0


def _lum(r: float, g: float, b: float) -> float:
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def too_light(data: bytes) -> bool:
    """Beyaz panelde kaybolacak (beyaz/çok açık renkli) logo mu? Saydam arka planlı görsellerde "mürekkep" pikselleri
    sayılır: çoğu neredeyse beyazsa True. Opak (beyaz zeminli JPEG gibi) görsellerde False: zemin zaten beyazdır."""
    if is_svg(data):
        txt = data.decode("utf-8", "ignore")
        cols = re.findall(r'(?:fill|stop-color)\s*[:=]\s*["\']?\s*(#[0-9a-fA-F]{3,6}\b|white|rgb\([^)]*\))', txt)
        vals = []
        for c in cols:
            c = c.lower()
            if c == "white":
                vals.append(1.0)
            elif c.startswith("rgb"):
                n = [float(x) for x in re.findall(r"[\d.]+", c)[:3]]
                vals.append(_lum(*n) if len(n) == 3 else 0.0)
            else:
                h = c[1:]
                h = "".join(ch * 2 for ch in h) if len(h) == 3 else h
                vals.append(_lum(*(int(h[i:i + 2], 16) for i in (0, 2, 4))) if len(h) == 6 else 0.0)
        return bool(vals) and sum(v > 0.9 for v in vals) / len(vals) >= 0.6
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data)).convert("RGBA")
        im.thumbnail((120, 120))
        raw = im.tobytes()
        px = [(raw[i], raw[i + 1], raw[i + 2], raw[i + 3]) for i in range(0, len(raw), 4)]
        if sum(1 for p in px if p[3] < 40) / len(px) < 0.05:
            return False                       # saydam bölge yok: zemin görselin kendisinde
        ink = [_lum(*p[:3]) for p in px if p[3] >= 40]
        if not ink:
            return True
        light = sum(1 for v in ink if v > 0.9) / len(ink)
        dark = sum(1 for v in ink if v < 0.25) / len(ink)
        return light > 0.25 and light > dark
    except Exception:  # noqa: BLE001 - Pillow yoksa/bozuk dosya: kontrol atlanır
        return False


# --- ağ yardımcıları ------------------------------------------------------------------------

def _wm(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return host.endswith(("wikimedia.org", "wikipedia.org", "wikidata.org"))


def _get(url: str, params: dict | None = None, timeout: int = 20, **kw) -> requests.Response:
    """GET; Wikimedia sunucuları için açıklayıcı User-Agent, 429/503'te kısa bekleyip en çok 3 deneme."""
    headers = {"User-Agent": UA if _wm(url) else BROWSER_UA, **kw.pop("headers", {})}
    r = requests.get(url, params=params, headers=headers, timeout=timeout, **kw)
    for attempt in (1, 2):
        if r.status_code not in (429, 503) or not _wm(url):
            break
        try:
            wait = min(float(r.headers.get("retry-after", "") or 0), 8.0)
        except (TypeError, ValueError):
            wait = 0.0
        time.sleep(wait or 2.0 * attempt)
        r = requests.get(url, params=params, headers=headers, timeout=timeout, **kw)
    return r


def _fetch(url: str, min_side: int = 100, timeout: int = 20) -> bytes | None:
    try:
        r = _get(url, timeout=timeout, headers={"Accept": "image/*,*/*;q=0.5"}, allow_redirects=True)
    except requests.RequestException as e:
        _t(f"indirilemedi {url[:90]}: {type(e).__name__}")
        return None
    ctype = (r.headers.get("content-type") or "").lower()
    if r.status_code != 200 or not (400 < len(r.content) < 8_000_000):
        _t(f"görsel yok {url[:90]}: HTTP {r.status_code}")
        return None
    if not (ctype.startswith("image/") or "svg" in ctype or url.lower().split("?")[0].endswith(".svg")):
        _t(f"görsel değil {url[:90]}: {ctype[:40]}")
        return None
    out = norm_image(r.content, min_side)
    if out is None:
        _t(f"görsel uygun değil (küçük/bozuk) {url[:90]}")
    return out


def _page(url: str, timeout: int = 12) -> tuple[str, str] | None:
    try:
        r = _get(url, timeout=timeout, headers={"Accept-Language": "tr,en;q=0.8"}, allow_redirects=True)
    except requests.RequestException as e:
        _t(f"site açılmadı {url[:70]}: {type(e).__name__}")
        return None
    if r.status_code != 200 or "html" not in (r.headers.get("content-type") or "html").lower():
        _t(f"site açılmadı {url[:70]}: HTTP {r.status_code}")
        return None
    return r.url, r.text[:700_000]


def _api(host: str, params: dict) -> dict:
    r = _get(f"https://{host}/w/api.php", params={"format": "json", **params}, timeout=25)
    r.raise_for_status()
    return r.json()


# --- 1) elle eklenen logo -------------------------------------------------------------------

def override_logo(org: str) -> bytes | None:
    s = slug(org)
    if not s or not ORG_LOGO_DIR.is_dir():
        return None
    for ext in ("png", "svg", "jpg", "jpeg", "webp"):
        f = ORG_LOGO_DIR / f"{s}.{ext}"
        if f.is_file():
            return norm_image(f.read_bytes())
    return None


# --- 2) kaynak sayfa (Youthall) -------------------------------------------------------------

def source_hints(url: str, org: str = "") -> dict:
    """Youthall ilan sayfası: şirket logosu + sayfadaki dış (şirket) bağlantıları. Diğer kaynaklar için boş."""
    if not url or domain_of(url) != "youthall.com":
        return {}
    pg = _page(url)
    if not pg:
        return {}
    soup = BeautifulSoup(pg[1], "html.parser")
    toks = [t for t in fold(org) if t not in GENERIC and len(t) >= 3]
    imgs = soup.find_all("img", src=re.compile(r"/media/images/company/image/"))
    logo = ""
    for img in imgs:                                           # önce alt metni kurum adını içeren, yoksa ilk görsel
        if any(t in " ".join(fold(img.get("alt", ""))) for t in toks):
            logo = img["src"]
            break
    if not logo and imgs:
        logo = imgs[0]["src"]
    alt = next((i.get("alt", "") for i in soup.find_all("img", src=logo) if i.get("alt")), "") if logo else ""
    name = re.sub(r"\s*logo\w*\s*$", "", alt, flags=re.I).strip()          # "Shell logo" -> "Shell"
    sites: list[str] = []
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if not h.startswith("http") or any(s in urlsplit(h).netloc.lower() for s in SKIP_HOSTS):
            continue
        origin = "{0.scheme}://{0.netloc}/".format(urlsplit(h))
        if origin not in sites:
            sites.append(origin)
    return {"logo_url": logo, "sites": sites[:4], "org_name": name}


def resolve_org(org: str, source_name: str) -> str:
    """Youthall sayfasındaki şirket adı (logo alt metni) modelin verdiği kurum adıyla çelişiyorsa ya da kurum adı boşsa/"Youthall"sa
    sayfadaki ad kullanılır (ör. model "Akaryakıt Dışı Gelirler" demiş, sayfa "Shell")."""
    org, source_name = (org or "").strip(), (source_name or "").strip()
    if not source_name:
        return org
    if not org or fold(org) == ["youthall"]:
        return source_name
    toks = [t for t in fold(org) if t not in GENERIC and len(t) >= 3]
    src = " ".join(fold(source_name))
    return org if (not toks or any(t in src for t in toks) or " ".join(fold(source_name)) in " ".join(fold(org))) else source_name


def hints_for(post: dict, cfg: dict) -> dict:
    """İlandan logo ipuçları: kaynak sayfa logosu + olası resmî site adresleri (hepsi sonradan doğrulanır)."""
    from .article import blocked_domains

    TRACE.clear()                      # her ilan için yeni günlük (find_org_visual'dan önce çağrılır)
    org = post.get("organization", "")
    hints: dict = {"logo_url": post.get("org_logo_url") or "", "sites": [], "trusted_sites": []}
    if post.get("organization_domain"):
        hints["sites"].append(post["organization_domain"])
    try:
        sh = source_hints(post.get("source_url", ""), org)
    except Exception as e:  # noqa: BLE001 - ipucu alınamadıysa diğer kaynaklar denenir
        _t(f"kaynak sayfa ipucu alınamadı: {type(e).__name__}")
        sh = {}
    hints["logo_url"] = hints["logo_url"] or sh.get("logo_url", "")
    hints["org_name"] = sh.get("org_name", "")
    hints["trusted_sites"] += sh.get("sites", [])          # Youthall şirket sayfasındaki bağlantılar: güvenilir kaynak
    apply, blocked = post.get("apply_url") or "", blocked_domains(cfg, post.get("source_url", ""))
    if apply and domain_of(apply) not in blocked and not any(s in urlsplit(apply).netloc.lower() for s in SKIP_HOSTS):
        hints["sites"].append(apply)
    return hints


# --- 3) Vikipedi ----------------------------------------------------------------------------

def search_pages(lang: str, org: str) -> list[dict]:
    j = _api(f"{lang}.wikipedia.org", {
        "action": "query", "generator": "search", "gsrsearch": org, "gsrlimit": 3, "gsrnamespace": 0,
        "prop": "pageimages|extracts", "piprop": "original|name|thumbnail", "pithumbsize": THUMB,
        "pilimit": 3, "exintro": 1, "explaintext": 1, "exchars": 400, "exlimit": 3})
    return sorted(((j.get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 99))


def file_thumb(host: str, filename: str) -> str | None:
    """Wikimedia dosyasının küçük resim adresi (SVG'de PNG olarak çizilmiş hâli)."""
    j = _api(host, {"action": "query", "titles": "File:" + filename, "prop": "imageinfo", "iiprop": "url|size",
                    "iiurlwidth": THUMB})
    page = next(iter(((j.get("query") or {}).get("pages") or {}).values()), {})
    ii = (page.get("imageinfo") or [{}])[0]
    return ii.get("thumburl") or ii.get("url")


def _relevant_file(filename: str, org: str) -> bool:
    """Dosya adı kurumla ilgili mi? (kurum adının ayırt edici kelimelerinden biri ya da baş harfleri). Başka kuruluşların
    amblemi (ör. UNHCR sayfasındaki BM amblemi) bu sayede elenir."""
    toks = [t for t in fold(org) if t not in GENERIC and len(t) >= 2]
    f = fold(re.sub(r"\.\w+$", "", filename))
    initials = "".join(t[0] for t in fold(org)) if len(fold(org)) >= 3 else ""
    return any(t in f or (len(t) >= 4 and any(w.startswith(t) or t.startswith(w) and len(w) >= 4 for w in f)) for t in toks) or (
        len(initials) >= 3 and initials in f)


def page_logo_file(lang: str, title: str, org: str = "") -> str | None:
    """Sayfanın ana görseli logo değilse: sayfadaki görseller arasında adı "logo" içeren VE kurumla ilgili dosya."""
    j = _api(f"{lang}.wikipedia.org", {"action": "query", "titles": title, "prop": "images", "imlimit": 80})
    page = next(iter(((j.get("query") or {}).get("pages") or {}).values()), {})
    for im in page.get("images") or []:
        f = (im.get("title") or "").split(":", 1)[-1]
        if (LOGO_HINT.search(f) and not NOT_VISUAL.search(f) and not WIKI_BOILER.search(f)
                and f.lower().endswith((".svg", ".png", ".jpg", ".jpeg")) and _relevant_file(f, org or title)):
            return f
    return None


def file_license(host: str, filename: str) -> dict:
    j = _api(host, {"action": "query", "titles": "File:" + filename, "prop": "imageinfo", "iiprop": "extmetadata",
                    "iiextmetadatafilter": "Artist|LicenseShortName|NonFree"})
    page = next(iter(((j.get("query") or {}).get("pages") or {}).values()), {})
    em = ((page.get("imageinfo") or [{}])[0]).get("extmetadata") or {}
    val = lambda k: (em.get(k) or {}).get("value", "")  # noqa: E731
    artist = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", val("Artist")))).strip()
    return {"artist": artist, "license": val("LicenseShortName").strip(), "nonfree": str(val("NonFree")).lower() in ("true", "1", "yes")}


def _wikipedia(org: str, strong: bool = False) -> tuple[dict | None, dict | None]:
    """Dönüş: (logo sonucu | None, fotoğraf adayı | None)."""
    photo_cand = None
    for lang in LANGS:
        try:
            pages = search_pages(lang, org)
        except (requests.RequestException, ValueError) as e:
            _t(f"Vikipedi ({lang}) aranamadı: {type(e).__name__}")
            continue
        matched = [p for p in pages if name_matches(org, p.get("title", ""), p.get("index", 99), p.get("extract", ""), strong)]
        _t(f"Vikipedi ({lang}): {len(pages)} sayfa, {len(matched)} tanesi kurum adıyla eşleşti" +
           (f" ({matched[0].get('title')})" if matched else ""))
        for p in matched:
            fname, thumb, orig = p.get("pageimage"), (p.get("thumbnail") or {}).get("source"), p.get("original") or {}
            kind = classify(fname, orig.get("width", 0), orig.get("height", 0)) if fname and thumb else None
            url = f"https://{lang}.wikipedia.org/wiki/" + p.get("title", "").replace(" ", "_")
            if kind == "logo":
                data = _fetch(thumb, min_side=0)
                if data:
                    return {"kind": "logo", "bytes": data, "credit": "", "title": p.get("title", ""),
                            "source": f"Vikipedi ({lang})", "url": url}, photo_cand
            try:                                    # ana görsel logo değil: sayfadaki "logo" dosyası var mı?
                lf = page_logo_file(lang, p.get("title", ""), org)
                lthumb = file_thumb(f"{lang}.wikipedia.org", lf) if lf else None
            except (requests.RequestException, ValueError) as e:
                _t(f"Vikipedi sayfa görselleri alınamadı: {type(e).__name__}")
                lthumb = None
            if lthumb:
                data = _fetch(lthumb, min_side=0)
                if data:
                    return {"kind": "logo", "bytes": data, "credit": "", "title": p.get("title", ""),
                            "source": f"Vikipedi ({lang}) sayfa logosu", "url": url}, photo_cand
            if kind == "photo" and photo_cand is None:
                photo_cand = {"lang": lang, "fname": fname, "thumb": thumb, "title": p.get("title", ""), "url": url}
    return None, photo_cand


def _wikipedia_photo(cand: dict) -> dict | None:
    host = f"{cand['lang']}.wikipedia.org"
    try:
        lic = file_license(host, cand["fname"])
    except (requests.RequestException, ValueError) as e:
        _t(f"fotoğraf lisansı alınamadı: {type(e).__name__}")
        return None
    if lic["nonfree"] or not OK_LICENSE.match(lic["license"]):
        _t(f"Vikipedi fotoğrafı lisansı uygun değil ({lic['license'] or '?'}): {cand['fname']}")
        return None
    try:
        r = _get(cand["thumb"], timeout=60)
    except requests.RequestException as e:
        _t(f"Vikipedi fotoğrafı indirilemedi: {type(e).__name__}")
        return None
    if r.status_code != 200 or not (r.headers.get("content-type") or "").startswith("image/") or len(r.content) < 20_000:
        return None
    credit = " / ".join(x for x in (lic["artist"][:60], f"Wikimedia Commons ({lic['license']})") if x)
    return {"kind": "photo", "bytes": r.content, "credit": credit, "title": cand["title"],
            "source": f"Vikipedi ({cand['lang']}) fotoğraf", "url": cand["url"]}


# --- 4) Wikidata ----------------------------------------------------------------------------

def _claim(claims: dict, pid: str) -> str | None:
    best = None
    for c in claims.get(pid) or []:
        v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if not isinstance(v, str) or c.get("rank") == "deprecated":
            continue
        ended = "P582" in (c.get("qualifiers") or {})
        if best is None or (best[1] and not ended) or c.get("rank") == "preferred":
            best = (v, ended)
    return best[0] if best else None


def wikidata_lookup(org: str, strong: bool = False) -> dict:
    """{"logo_file": Commons dosya adı | None, "site": resmî site | None}. Ad eşleşmesi kodla doğrulanır."""
    for lang in LANGS:
        try:
            j = _api("www.wikidata.org", {"action": "wbsearchentities", "search": org, "language": lang, "uselang": lang,
                                          "limit": 5, "type": "item"})
            ids = [s["id"] for s in j.get("search") or []]
            if not ids:
                continue
            g = _api("www.wikidata.org", {"action": "wbgetentities", "ids": "|".join(ids), "props": "claims|labels|aliases|descriptions",
                                          "languages": "tr|en"})
        except (requests.RequestException, ValueError, KeyError) as e:
            _t(f"Wikidata ({lang}) aranamadı: {type(e).__name__}")
            continue
        for rank, qid in enumerate(ids, start=1):
            e = (g.get("entities") or {}).get(qid) or {}
            desc = " ".join(v.get("value", "") for v in (e.get("descriptions") or {}).values())
            if BAD_WIKIDATA.search(desc):
                continue
            names = [v["value"] for v in (e.get("labels") or {}).values()]
            names += [a["value"] for lst in (e.get("aliases") or {}).values() for a in lst]
            if not any(name_matches(org, n, rank, "", strong) for n in names):
                continue
            claims = e.get("claims") or {}
            logo, site = _claim(claims, "P154"), _claim(claims, "P856")
            _t(f"Wikidata {qid} eşleşti: logo={logo or '-'} site={site or '-'}")
            if logo or site:
                return {"logo_file": logo, "site": site}
    return {"logo_file": None, "site": None}


# --- 5) kurumun kendi sitesi ----------------------------------------------------------------

def site_matches(org: str, page_html: str, url: str, strict: bool = True) -> bool:
    """Sayfa gerçekten bu kuruma mı ait? Başlık/site adı/açıklama/alan adında kurum adının ayırt edici kelimeleri aranır.
    strict=True (tahmin edilen ya da modelin önerdiği alan adı): 2+ kelimeli adlarda HEPSİ geçmeli (ör. "Mevlana Kalkınma Ajansı"
    için sadece "Mevlana" geçen başka bir site kabul edilmez). strict=False (Wikidata/Youthall gibi güvenilir kaynaktan gelen site)."""
    toks = [t for t in fold(org) if t not in GENERIC and t not in SOFT and len(t) >= 2] or fold(org)
    if not toks:
        return False
    soup = BeautifulSoup(page_html, "html.parser")
    if PARKING.search((soup.title.get_text(" ") if soup.title else "") + " " + soup.get_text(" ", strip=True)[:1500]):
        return False                                   # satılık/park edilmiş alan adı: kurumun sitesi değil
    bag = [soup.title.get_text(" ") if soup.title else "", urlsplit(url).netloc.replace(".", " ").replace("-", " ")]
    for m in soup.find_all("meta"):
        if (m.get("property") or m.get("name") or "").lower() in ("og:site_name", "og:title", "application-name", "description", "og:description"):
            bag.append(m.get("content") or "")
    words = fold(" ".join(bag))
    joined = " ".join(words)
    if " ".join(fold(org)) in joined:
        return True
    hit = sum(1 for t in toks if t in words or (len(t) >= 4 and any(w.startswith(t) for w in words)))
    if strict:
        return hit == len(toks)
    return hit >= 1 if len(toks) <= 2 else hit / len(toks) >= 0.75


def logo_candidates(page_html: str, base: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    out: list[str] = []

    def add(u: str | None) -> None:
        u = (u or "").strip()
        if not u or u.startswith("data:"):
            return
        absu = urljoin(base, u)
        if urlsplit(absu).scheme in ("http", "https") and absu not in out:
            out.append(absu)

    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):          # 1) JSON-LD logo
        try:
            data = json.loads(s.string or s.get_text() or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, list):
                stack.extend(n)
            elif isinstance(n, dict):
                lg = n.get("logo")
                add(lg if isinstance(lg, str) else (lg.get("url") or lg.get("contentUrl")) if isinstance(lg, dict) else None)
                stack.extend(v for v in n.values() if isinstance(v, (list, dict)))

    n_img = 0
    for img in soup.find_all("img", limit=150):                                      # 2) sayfadaki logo görseli
        chain = []
        for par in list(img.parents)[:3]:
            chain += (par.get("class") or []) + [par.get("id") or ""]
        blob = " ".join([img.get("alt") or "", img.get("src") or "", img.get("data-src") or "", " ".join(img.get("class") or []),
                         img.get("id") or "", " ".join(chain)])
        if not re.search(r"(logo|navbar-brand|site-title|custom-logo|brand)", blob, re.I) or BAD_LOGO_CTX.search(blob):
            continue
        srcset = (img.get("srcset") or "").split(",")[0].strip().split(" ")[0]
        add(img.get("src") or img.get("data-src") or srcset)
        n_img += 1
        if n_img >= 4:
            break

    icons = []                                                                       # 3) apple-touch-icon / büyük ikon
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "icon" not in rel or link["href"].lower().split("?")[0].endswith(".ico"):
            continue
        m = re.match(r"(\d+)x\d+", (link.get("sizes") or "").lower())
        icons.append((int(m.group(1)) if m else 0, "apple" in rel, link["href"]))
    for _, _, href in sorted(icons, key=lambda x: (-x[0], not x[1]))[:2]:
        add(href)
    add("/apple-touch-icon.png")

    og = soup.find("meta", attrs={"property": "og:image"})                           # 4) og:image yalnızca adında "logo" varsa
    if og and "logo" in (og.get("content") or "").lower():
        add(og["content"])
    return out


def _variants(site: str) -> list[str]:
    """Bazı sitelerde yalnızca www'li (ya da www'siz) adres sertifikalı/çalışır: sırayla denenir."""
    sp = urlsplit(site)
    host = sp.netloc
    alt = host[4:] if host.startswith("www.") else "www." + host
    return [f"{sp.scheme}://{host}/", f"{sp.scheme}://{alt}/", f"http://{host}/", f"http://{alt}/"]


def site_logo(org: str, site: str, strict: bool = True) -> dict | None:
    site = (site or "").strip()
    if not site:
        return None
    if not site.startswith("http"):
        site = "https://" + site
    pg = None
    for v in _variants(site):
        pg = _page(v)
        if pg:
            break
    if not pg:
        return None
    final, doc = pg
    if not site_matches(org, doc, final, strict):
        _t(f"site kurumla eşleşmedi, atlandı: {urlsplit(final).netloc}")
        return None
    cands = logo_candidates(doc, final)
    host = urlsplit(final).netloc
    _t(f"site doğrulandı: {host}; {len(cands)} logo adayı")
    first = None
    for u in cands[:8]:                                  # sayfanın kendi sırası: JSON-LD logo, üstbilgi logosu, ikonlar
        data = _fetch(u, min_side=100)
        if data:
            first = (data, u)
            break
    if first:
        return {"kind": "logo", "bytes": first[0], "credit": "", "title": host, "source": f"web sitesi ({host})", "url": final}
    data = _fetch(f"https://www.google.com/s2/favicons?domain={host}&sz=256", min_side=100)   # son çare: büyük favicon
    if data:
        return {"kind": "logo", "bytes": data, "credit": "", "title": host, "source": f"web sitesi ikonu ({host})", "url": final}
    return None


def guess_sites(org: str) -> list[str]:
    """Kurum adından tahmin edilen alan adları (tek/iki ayırt edici kelime). Hepsi site_matches ile doğrulanır."""
    toks = [t for t in fold(org) if t not in GENERIC]
    if not toks or len(toks) > 2 or len(toks[0]) < 4:
        return []
    bases = [toks[0]] + (["".join(toks)] if len(toks) == 2 else [])
    return [f"https://www.{b}{tld}/" for b in bases for tld in (".com.tr", ".com", ".org.tr", ".gov.tr", ".org", ".edu.tr", ".net")]


def _sites_from(candidates: list[str]) -> list[str]:
    seen, out = set(), []
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        u = c if c.startswith("http") else "https://" + c
        sp = urlsplit(u)
        origin = f"{sp.scheme}://{sp.netloc}/"
        if sp.netloc and origin not in seen:
            seen.add(origin)
            out.append(origin)
    return out


# --- ana akış -------------------------------------------------------------------------------

def find_org_visual(org: str, cfg: dict, hints: dict | None = None) -> dict | None:
    """Dönüş: {"kind": "logo"|"photo", "bytes", "credit", "title", "source", "url"} ya da None (adım adım günlük: TRACE)."""
    org = (org or "").strip()
    hints = hints or {}
    if not (org or hints.get("logo_url")) or not (cfg.get("org_visual") or {}).get("enabled", True):
        return None

    def logo(data: bytes, source: str, url: str = "") -> dict:
        return {"kind": "logo", "bytes": data, "credit": "", "title": org, "source": source, "url": url}

    data = override_logo(org)                                                       # 1
    if data:
        _t("elle eklenen logo kullanıldı")
        return logo(data, "elle eklenen logo")

    weak = None                                                                     # 2
    if hints.get("logo_url"):
        data = _fetch(hints["logo_url"], min_side=100)
        if data and low_res(data):
            _t(f"kaynak sayfa logosu küçük ({int(image_size(data)[0])}px), daha iyisi aranacak: {hints['logo_url'][:80]}")
            weak = logo(data, "ilan sayfasındaki şirket logosu", hints["logo_url"])
        elif data:
            _t(f"kaynak sayfa logosu: {hints['logo_url'][:90]}")
            return logo(data, "ilan sayfasındaki şirket logosu", hints["logo_url"])

    def best() -> dict | None:
        if weak:
            _t("daha iyi logo bulunamadı, küçük kaynak sayfa logosu kullanılıyor")
        return weak

    if not org:
        _t("kurum adı yok, logo yalnızca kaynak sayfadan denenebilirdi")
        return best()
    strong = weak is not None          # elde ilan sayfasının (doğru ama küçük) logosu var: yalnızca güçlü ad eşleşmeleri onu değiştirir
    found, photo_cand = _wikipedia(org, strong)                                     # 3
    if found:
        return found

    wd = {"logo_file": None, "site": None}                                          # 4
    try:
        wd = wikidata_lookup(org, strong)
    except Exception as e:  # noqa: BLE001
        _t(f"Wikidata hatası: {type(e).__name__}")
    if wd["logo_file"]:
        try:
            thumb = file_thumb("commons.wikimedia.org", wd["logo_file"])
            data = _fetch(thumb, min_side=0) if thumb else None
        except (requests.RequestException, ValueError) as e:
            _t(f"Wikidata logosu alınamadı: {type(e).__name__}")
            data = None
        if data:
            return logo(data, "Wikidata logosu", "https://www.wikidata.org/")

    trusted = _sites_from(list(hints.get("trusted_sites") or []) + ([wd["site"]] if wd["site"] else []))   # 5
    others = [o for o in _sites_from(list(hints.get("sites") or [])) if o not in trusted]
    for origin, strict in [(o, False) for o in trusted] + [(o, True) for o in others]:
        res = site_logo(org, origin, strict)
        if res:
            return res
    guesses = [g for g in guess_sites(org) if g not in trusted + others]
    if guesses:
        with ThreadPoolExecutor(max_workers=6) as ex:
            pages = list(ex.map(lambda g: _page(g, timeout=8), guesses))
        for g, pg in zip(guesses, pages):
            if pg and site_matches(org, pg[1], pg[0], strict=True):
                res = site_logo(org, g, strict=True)
                if res:
                    return res

    if weak:
        return best()
    if photo_cand:                                                                  # 6
        res = _wikipedia_photo(photo_cand)
        if res:
            return res
    _t("logo bulunamadı")
    return None
