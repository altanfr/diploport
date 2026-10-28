"""Kalıcı durum: görülenler, kuyruk, onay bekleyenler. Tek bir JSON dosyası."""
from __future__ import annotations

from datetime import datetime, timedelta

from . import util
from .util import now_utc, read_json, write_json

def STATE_PATH():
    return util.DATA_DIR / "state.json"


def load_state() -> dict:
    s = read_json(STATE_PATH(), {})
    s.setdefault("seen", {})        # id -> {"ts","status","reason","title"}
    s.setdefault("queue", [])       # paylaşıma hazır ilanlar
    s.setdefault("pending", {})     # id -> ilan (Telegram onayı bekliyor)
    s.setdefault("published", [])   # [{"id","ts","official"}] son paylaşımlar
    s.setdefault("official_seen", [])  # tekilleştirme için resmî link anahtarları
    s.setdefault("outbox", [])      # hazırlanmış, Instagram'a gönderilmeyi bekleyenler
    s.setdefault("fails", {})       # id -> sayfa açma hata sayısı
    s.setdefault("titles", [])      # tekilleştirme için normalize başlıklar
    s.setdefault("tg_offset", 0)
    return s


def save_state(state: dict) -> None:
    # Dosya şişmesin: eski "seen" kayıtlarını 120 günden sonra buda.
    cutoff = (now_utc() - timedelta(days=120)).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v.get("ts", "") >= cutoff}
    state["published"] = state["published"][-200:]
    state["official_seen"] = state["official_seen"][-1000:]
    state["titles"] = state["titles"][-400:]
    write_json(STATE_PATH(), state)


def mark(state: dict, item_id: str, status: str, reason: str = "", title: str = "") -> None:
    state["seen"][item_id] = {"ts": now_utc().isoformat(), "status": status, "reason": reason, "title": title[:120]}


def age_days(iso_ts: str) -> float:
    try:
        return (now_utc() - datetime.fromisoformat(iso_ts)).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.0
