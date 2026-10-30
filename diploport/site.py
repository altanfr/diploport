"""Statik site verisi: docs/posts.json. Site (docs/index.html) bu dosyayı okuyup kendini çizer."""
from __future__ import annotations

from pathlib import Path

from . import util
from .util import now_utc, read_json, write_json

THUMB_WIDTH = 540          # sitedeki kayan kartlar için küçük sürüm (telefonda hızlı açılsın)

PUBLIC_FIELDS = [
    "id", "title_tr", "summary_tr", "highlights_tr", "category", "organization", "country_tr",
    "deadline", "funding_tr", "fee_required", "apply_url", "source",
]


def posts_path():
    return util.DOCS_DIR / "posts.json"


def load_posts() -> list[dict]:
    return read_json(posts_path(), [])


def make_thumb(src: Path, dst: Path, width: int = THUMB_WIDTH) -> bool:
    """Kart görselinin küçük kopyasını yazar. Başarısızsa False (site tam boy görsele döner)."""
    from PIL import Image

    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst, "JPEG", quality=80, optimize=True, progressive=True)
        return True
    except Exception:  # noqa: BLE001 - küçük görsel olmazsa da ilan yayımlanır
        return False


def add_post(post: dict, max_posts: int = 300) -> None:
    posts = [p for p in load_posts() if p["id"] != post["id"]]
    entry = {k: post.get(k) for k in PUBLIC_FIELDS}
    entry["published_at"] = now_utc().isoformat()
    entry["image"] = f"images/{post['id']}.jpg"
    if make_thumb(util.DOCS_DIR / entry["image"], util.DOCS_DIR / "images" / "t" / f"{post['id']}.jpg"):
        entry["thumb"] = f"images/t/{post['id']}.jpg"
    posts.insert(0, entry)
    write_json(posts_path(), posts[:max_posts])


def prune_images() -> int:
    """posts.json'da artık olmayan görselleri siler (depo şişmesin)."""
    posts = load_posts()
    keep = {p["image"].split("/")[-1] for p in posts}
    keep_thumbs = {p["thumb"].split("/")[-1] for p in posts if p.get("thumb")}
    img_dir = util.DOCS_DIR / "images"
    removed = 0
    for folder, names in ((img_dir, keep), (img_dir / "t", keep_thumbs)):
        if folder.exists():
            for f in folder.glob("*.jpg"):
                if f.name not in names:
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
