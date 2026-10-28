"""Ortak yardımcılar: yollar, JSON, URL ve metin normalizasyonu."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DIPLOPORT_DATA_DIR", ROOT / "data"))
DOCS_DIR = Path(os.environ.get("DIPLOPORT_DOCS_DIR", ROOT / "docs"))

USER_AGENT = "Mozilla/5.0 (compatible; DiploportBot/1.0; +https://github.com/)"

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid"}
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac", "gen", "k12", "bel", "av"}


def load_config(path: Path | None = None) -> dict:
    path = path or ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_tr(cfg: dict) -> datetime:
    return datetime.now(ZoneInfo(cfg.get("timezone", "Europe/Istanbul")))


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj) -> None:
    """Atomik yazım: yarım dosya kalmasın."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def registrable_domain(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_of(url: str) -> str:
    return registrable_domain(urlsplit(url).netloc)


def clean_url(url: str) -> str:
    """İzleme parametrelerini (utm_*, fbclid...) ve #parçayı temizler."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def canonical_url(url: str) -> str:
    """Tekilleştirme anahtarı: küçük harf host, www yok, sondaki / yok, izleme yok."""
    parts = urlsplit(clean_url(url))
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    return f"{host}{path}" + (f"?{parts.query}" if parts.query else "")


def short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def norm_text(text: str) -> str:
    """Kanıt doğrulaması için: büyük/küçük harf, tırnak, boşluk farklarını yok say."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    text = text.casefold()
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def fmt_date_tr(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    return f"{d.day} {TR_MONTHS[d.month - 1]} {d.year}"
