"""Yayın katmanı: Instagram yazısı, Make webhook'u, Telegram onayı."""
from __future__ import annotations

import html
import logging
import os
import time

import requests

from .card import category_label
from .util import USER_AGENT, fmt_date_tr

log = logging.getLogger("diploport.publish")


# --- Instagram yazısı --------------------------------------------------------

def build_hashtags(post: dict, cfg: dict, limit: int = 8) -> str:
    tags = list(cfg["hashtags"].get("common", [])) + list(cfg["hashtags"].get(post.get("category", "diger"), []))
    seen, out = set(), []
    for t in tags:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return " ".join(out[:limit])


def build_caption(post: dict, cfg: dict) -> str:
    """Instagram açıklaması (max 2200 karakter). Not: Instagram açıklamadaki linkleri tıklanabilir yapmaz;
    bu yüzden hem resmî link hem de sitedeki ilan sayfası yazılır."""
    site = cfg["brand"]["site_url"].rstrip("/")
    lines = [post["title_tr"], "", post["summary_tr"], ""]
    lines += [f"- {h}" for h in post.get("highlights_tr", [])[:3]]
    lines.append("")
    if post.get("deadline"):
        lines.append(f"Son başvuru: {fmt_date_tr(post['deadline'])}")
    lines.append(f"Başvuru: {post['apply_url']}")
    lines.append(f"Tüm fırsatlar: {site}/#{post['id']}")
    lines += ["", f"Kaynak: {post['source']}"]
    if post.get("photo_credit"):  # Pexels kullanım yönergesi: fotoğrafçıyı ve Pexels'i an
        lines.append(f"Görsel: {post['photo_credit']}")
    lines += ["", build_hashtags(post, cfg)]
    caption = "\n".join(lines)
    if len(caption) > 2200:  # çok uzunsa önce vurguları at
        lines = [l for l in lines if not l.startswith("- ")]
        caption = "\n".join(lines)[:2200]
    return caption


# --- Make webhook'u (Instagram'a gönderen küçük senaryo) ----------------------

def post_to_make(payload: dict, retries: int = 3) -> None:
    url = os.environ.get("MAKE_WEBHOOK_URL", "")
    if not url:
        raise RuntimeError("MAKE_WEBHOOK_URL tanımlı değil")
    payload = dict(payload, secret=os.environ.get("MAKE_SECRET", ""))
    last = ""
    for i in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=60)
            if r.status_code == 200:
                return
            last = f"HTTP {r.status_code}: {r.text[:150]}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"Make webhook başarısız: {last}")


def wait_public(url: str, timeout: int = 480, interval: int = 15) -> bool:
    """GitHub Pages yayına alınana kadar bekler (Instagram görseli herkese açık bir URL'den çeker)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20, stream=True)
            ok = r.status_code == 200 and "image" in r.headers.get("content-type", "")
            r.close()
            if ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(interval)
    return False


# --- Telegram ----------------------------------------------------------------

def telegram_enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def _tg(method: str, **params) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


def notify(text: str) -> None:
    if not telegram_enabled():
        log.info("Telegram kapalı, bildirim: %s", text)
        return
    try:
        _tg("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], text=text[:4000], disable_web_page_preview=True)
    except Exception as e:  # bildirim hatası hattı durdurmasın
        log.warning("Telegram bildirimi gönderilemedi: %s", e)


def send_pending(item: dict, post: dict) -> int | None:
    """Uygunluğu belirsiz ilanı onay düğmeleriyle gönderir. message_id döndürür."""
    e = html.escape
    text = (
        f"<b>Onay bekliyor</b> · {e(category_label(post['category']))}\n\n"
        f"<b>{e(post['title_tr'])}</b>\n{e(post['summary_tr'])}\n\n"
        f"<b>Neden belirsiz:</b> {e(post.get('unclear_reason_tr', ''))}\n"
        f"Son başvuru: {e(fmt_date_tr(post.get('deadline', '')) or 'belirtilmemiş')} · Ülke: {e(post.get('country_tr', ''))}\n"
        f"Resmî link: {e(post['apply_url'])}\n"
        f"Kaynak: {e(item['source'])} — {e(item['link'])}"
    )
    try:
        res = _tg(
            "sendMessage",
            chat_id=os.environ["TELEGRAM_CHAT_ID"],
            text=text[:4000],
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup={"inline_keyboard": [[
                {"text": "Onayla", "callback_data": f"ok:{item['id']}"},
                {"text": "Reddet", "callback_data": f"no:{item['id']}"},
            ]]},
        )
        return res["message_id"]
    except Exception as ex:
        log.warning("Onay mesajı gönderilemedi: %s", ex)
        return None


def poll_decisions(state: dict) -> list[tuple[str, str]]:
    """Telegram'da basılmış Onayla/Reddet düğmelerini okur. [(eylem, ilan_id)] döndürür."""
    if not telegram_enabled():
        return []
    chat_id = int(os.environ["TELEGRAM_CHAT_ID"])
    try:
        updates = _tg("getUpdates", offset=state["tg_offset"], timeout=0, allowed_updates=["callback_query"])
    except Exception as e:
        log.warning("Telegram güncellemeleri okunamadı: %s", e)
        return []
    out = []
    for u in updates:
        state["tg_offset"] = max(state["tg_offset"], u["update_id"] + 1)
        cb = u.get("callback_query")
        if not cb or cb.get("from", {}).get("id") != chat_id:  # sadece sahibi
            continue
        action, _, item_id = (cb.get("data") or "").partition(":")
        if action not in ("ok", "no"):
            continue
        out.append((action, item_id))
        try:
            _tg("answerCallbackQuery", callback_query_id=cb["id"], text="Onaylandı, sıraya alındı" if action == "ok" else "Reddedildi")
            msg = cb.get("message") or {}
            _tg("editMessageReplyMarkup", chat_id=chat_id, message_id=msg.get("message_id"), reply_markup={"inline_keyboard": []})
        except Exception as e:
            log.debug("Telegram yanıt düzenlenemedi: %s", e)
    return out
