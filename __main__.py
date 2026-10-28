"""Komutlar:
  python -m diploport run         Tara, yargıla, kuyruğa/onaya al, sıradaki ilanın görselini hazırla
  python -m diploport send        Hazırlanan ilanı Instagram'a gönder (Make webhook'u)
  python -m diploport sample out.jpg [foto.jpg]   Örnek kart üret (tasarım önizleme; foto verilmezse Pexels/boş şablon)
Ortam değişkeni DRY_RUN=1: hiçbir şey kaydetme/gönderme, sadece ne yapılacağını raporla.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path

from .util import load_config


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(argv) < 2:
        print(__doc__)
        return 2
    cfg = load_config()
    cmd = argv[1]
    if cmd == "run":
        from .pipeline import run
        dry = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
        res = run(cfg, dry=dry)
        print(f"işlenen={res['processed']} hazırlanan={res['prepared']} kuyruk={res['queue']} onayda={res['pending']}")
        for row in res["report"]:
            print(" | ".join(str(x)[:90] for x in row))
        return 0
    if cmd == "send":
        if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
            print("DRY_RUN: gönderim atlandı")
            return 0
        from .pipeline import send
        print(f"gönderilen={send(cfg)}")
        return 0
    if cmd == "alert":
        from .publish import notify
        notify(" ".join(argv[2:]) or "Diploport uyarısı")
        return 0
    if cmd == "sample":
        from .card import render_card
        out = Path(argv[2] if len(argv) > 2 else "sample.jpg")
        photo = Path(argv[3]).read_bytes() if len(argv) > 3 else None  # isteğe bağlı: arka plan fotoğrafı dosyası
        render_card(
            {"id": "sample", "category": "burs", "title_tr": "Almanya'da Erasmus+ Gençlik Çalışanları Eğitimi",
             "organization": "DAAD", "deadline": "2026-12-15", "country_tr": "Almanya",
             "funding_tr": "Ücretsiz eğitim ve konaklama"},
            cfg, out, date.today(), photo)
        print(out)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
