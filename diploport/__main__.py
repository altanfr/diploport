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
    if cmd == "phototest":
        # Gerçek stok fotoğraf sağlayıcısını dener: 3 örnek ilan için kart üretir, data/photo_test/ altına yazar.
        from .card import render_card
        from . import photo as photo_mod, util
        out = util.DATA_DIR / "photo_test"
        out.mkdir(parents=True, exist_ok=True)
        samples = [
            {"id": "t1", "category": "egitim", "title_tr": "Almanya'da Erasmus+ Gençlik Çalışanları Eğitimi", "country_tr": "Almanya",
             "funding_tr": "Ücretsiz eğitim ve konaklama", "image_queries": ["Berlin Germany city", "Germany university students", "students walking campus"]},
            {"id": "t2", "category": "burs", "title_tr": "EJN 2027 Gazetecilik Bursu", "country_tr": "ABD",
             "funding_tr": "Tam burs", "image_queries": ["journalists newsroom", "United States journalism students", "reporters working laptop"]},
            {"id": "t3", "category": "is", "title_tr": "Kamu İhale Kurumu Uzman Yardımcılığı Giriş Sınavı", "country_tr": "Türkiye",
             "funding_tr": "Belirtilmemiş", "image_queries": ["Ankara government building", "Turkey office workers", "professional office meeting"]},
        ]
        have = [n for n, _ in photo_mod._providers(cfg)]
        lines = [f"Anahtarı tanımlı sağlayıcılar: {', '.join(have) or 'YOK'}", ""]
        for sp in samples:
            render_card(sp, cfg, out / f"{sp['id']}.jpg", date.today())
            lines.append(f"- {sp['id']}: sağlayıcı/künye={sp.get('photo_credit') or 'FOTOĞRAF YOK'} · arama={sp.get('photo_query') or '-'} · {sp.get('photo_url') or ''}")
        (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
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
