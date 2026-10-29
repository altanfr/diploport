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
        def inst(i, org, title, cat, qs, icon=""):
            d = {"id": i, "category": cat, "title_tr": title, "country_tr": "Türkiye", "funding_tr": "Belirtilmemiş",
                 "visual_kind": "institution", "organization": org, "image_queries": qs}
            if icon:
                d["icon"] = icon
            return d

        samples = [
            {"id": "t1", "category": "egitim", "title_tr": "Almanya'da Erasmus+ Gençlik Çalışanları Eğitimi", "country_tr": "Almanya",
             "funding_tr": "Ücretsiz eğitim ve konaklama", "visual_kind": "scene",
             "image_queries": ["Berlin Germany city", "Germany university students", "students walking campus"]},
            inst("i1", "KAMU İHALE KURUMU", "Kamu İhale Kurumu Uzman Yardımcılığı Giriş Sınavı", "is",
                 ["government building", "civil servants office", "modern office building"], "landmark"),
            inst("i2", "ORTA ANADOLU KALKINMA AJANSI", "Orta Anadolu Kalkınma Ajansı Personel Alımı", "is",
                 ["Kayseri city", "Ankara city skyline", "Turkey landscape"], "landmark"),
            inst("i3", "UNHCR", "UNHCR 2027 Staj Programı", "staj", ["Geneva city", "modern office building", "modern office building"]),
            inst("i4", "Oyak Renault", "Oyak Renault 4 Seasons Proje Stajyerliği", "staj", ["car factory", "engineers working factory", "modern office building"]),
            inst("i5", "Bilkom", "Bilkom Digital Leader Genç Yetenek Programı", "staj", ["software office", "developers working laptop", "modern office building"]),
            inst("i6", "TÜRKİYE İŞ KURUMU", "İŞKUR Uzman Yardımcısı Alımı", "is", ["government building", "civil servants office", "modern office building"], "landmark"),
            inst("i7", "Commencis", "Commencis Cloud Camp 2027", "egitim", ["Istanbul skyline", "Istanbul skyline", "Turkey landscape"]),
            inst("i8", "ORMAN GENEL MÜDÜRLÜĞÜ", "Orman Genel Müdürlüğü Müfettiş Yardımcısı Alımı", "is", ["Ankara city skyline", "Istanbul skyline", "Turkey landscape"], "landmark"),
            inst("i9", "Keyo People", "Keyo People Campus Kampüs Temsilciliği Programı", "staj", ["Istanbul skyline", "Istanbul skyline", "Turkey landscape"]),
        ]
        have = [n for n, _ in photo_mod._providers(cfg)]
        lines = [f"Anahtarı tanımlı sağlayıcılar: {', '.join(have) or 'YOK'}", ""]
        for sp in samples:
            render_card(sp, cfg, out / f"{sp['id']}.jpg", date.today())
            lines.append(f"- {sp['id']} ({sp.get('organization') or sp['title_tr']}): kaynak={sp.get('photo_query') or 'SİMGE KARTI'} · künye={sp.get('photo_credit') or '-'} · {sp.get('photo_url') or ''}")
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
             "funding_tr": "Ücretsiz eğitim ve konaklama", "visual_kind": "scene"},
            cfg, out, date.today(), photo)
        print(out)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
