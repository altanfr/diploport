"""Statik site verisi: docs/posts.json. Site (docs/index.html) bu dosyayı okuyup kendini çizer."""
from __future__ import annotations

from . import util
from .util import now_utc, read_json, write_json

PUBLIC_FIELDS = [
    "id", "title_tr", "summary_tr", "highlights_tr", "category", "organization", "country_tr",
    "deadline", "funding_tr", "fee_required", "apply_url", "source",
]


def posts_path():
    return util.DOCS_DIR / "posts.json"


def load_posts() -> list[dict]:
    return read_json(posts_path(), [])


def add_post(post: dict, max_posts: int = 300) -> None:
    posts = [p for p in load_posts() if p["id"] != post["id"]]
    entry = {k: post.get(k) for k in PUBLIC_FIELDS}
    entry["published_at"] = now_utc().isoformat()
    entry["image"] = f"images/{post['id']}.jpg"
    posts.insert(0, entry)
    write_json(posts_path(), posts[:max_posts])


def prune_images() -> int:
    """posts.json'da artık olmayan görselleri siler (depo şişmesin)."""
    keep = {p["image"].split("/")[-1] for p in load_posts()}
    img_dir = util.DOCS_DIR / "images"
    removed = 0
    if img_dir.exists():
        for f in img_dir.glob("*.jpg"):
            if f.name not in keep:
                f.unlink()
                removed += 1
    return removed


def write_site_config(cfg: dict) -> None:
    """Sitenin Instagram bağlantısı için küçük ayar dosyası."""
    handle = cfg["brand"].get("handle", "").lstrip("@")
    write_json(util.DOCS_DIR / "site.json", {
        "name": cfg["brand"]["name"],
        "instagram": f"https://instagram.com/{handle}" if handle else "",
    })
