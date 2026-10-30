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
        # Gerçek ağda deneme: örnek ilanların kartlarını üretir (data/photo_test/) ve her kurum için logonun NEREDEN geldiğini
        # (ya da neden bulunamadığını) adım adım raporlar.
        from .card import NoOrgVisual, render_card
        from . import orgvisual, util
        out = util.DATA_DIR / "photo_test"
        out.mkdir(parents=True, exist_ok=True)
        yt = "https://www.youthall.com/tr/"

        def inst(i, org, title="Staj ve Kariyer Programı", cat="staj", **extra):
            return {"id": i, "category": cat, "title_tr": title, "country_tr": "Türkiye", "funding_tr": "Belirtilmemiş",
                    "visual_kind": "institution", "organization": org, **extra}

        samples = [
            {"id": "t1", "category": "egitim", "title_tr": "Almanya'da Erasmus+ Gençlik Çalışanları Eğitimi", "country_tr": "Almanya",
             "funding_tr": "Ücretsiz eğitim ve konaklama", "visual_kind": "scene",
             "image_queries": ["Berlin Germany city", "Germany university students", "students walking campus"]},
            # kamu kurumları (Kariyer Kapısı): kurum adı ilan başlığından gelir, başka ipucu yok
            inst("k1", "KAMU İHALE KURUMU", cat="is"), inst("k2", "ORTA ANADOLU KALKINMA AJANSI", cat="is", organization_domain="oran.org.tr"),
            inst("k3", "TÜRKİYE İŞ KURUMU", cat="is"), inst("k4", "ORMAN GENEL MÜDÜRLÜĞÜ", cat="is"),
            inst("k5", "MEVLANA KALKINMA AJANSI", cat="is", organization_domain="mevka.org.tr"), inst("k6", "İSTANBUL KALKINMA AJANSI", cat="is"),
            inst("k7", "ORTA KARADENİZ KALKINMA AJANSI (OKA)", cat="is", organization_domain="oka.org.tr"), inst("k8", "MİLLİ SARAYLAR İDARESİ BAŞKANLIĞI", cat="is"),
            inst("k9", "BURDUR MEHMET AKİF ERSOY ÜNİVERSİTESİ REKTÖRLÜĞÜ", cat="is"),
            # uluslararası kuruluşlar / üniversite
            inst("o1", "UNHCR"), inst("o2", "UNICEF"), inst("o3", "United Nations Development Programme (UNDP)"),
            inst("o4", "United Nations Institute for Training and Research"), inst("o5", "Stanford University"),
            inst("o6", "Earth Journalism Network", organization_domain="earthjournalism.net"),
            # şirketler: Youthall ilan sayfasındaki şirket logosu
            inst("y1", "Akçansa", source_url=yt + "Akcansa/ready-mix-concrete-long-term-intern-thrace_58/"),
            inst("y2", "Aygaz", source_url=yt + "aygazdakariyer/satis-yoneticisi-adana_74/"),
            inst("y3", "Sefine Shipyard", source_url=yt + "sefine/sky-uzun-donem-staj-programi_5/"),
            inst("y4", "BSH Ev Aletleri", source_url="https://www.youthall.com/en/Bshik/we-power-2026_8/"),
            inst("y5", "Commencis", source_url="https://www.youthall.com/en/Commencis/commencis-cloud-camp-2027_50/"),
            inst("y6", "Akaryakıt Dışı Gelirler", source_url=yt + "Shell/pioneers-in-progress-long-term-internship-akaryakit-disi-gelirler_1320/"),
            inst("y7", "Henkel", source_url=yt + "Henkel/step-up-inclusive-engelli-bireylere-ozel_28/"),
            inst("y8", "Boehringer Ingelheim", source_url="https://www.youthall.com/en/boehringeringelheim/hr-intern_60/"),
            inst("y9", "Hyundai Motor Türkiye", source_url=yt + "hyundaimotorturkiye/hyundai-ideathon_5/"),
            # Youthall sayfası olmayan şirketler: Vikipedi/Wikidata/site/tahmin
            inst("c1", "Oyak Renault"), inst("c2", "Bilkom", organization_domain="bilkom.com.tr"), inst("c3", "Keyo People Campus"),
        ]
        lines = []
        for sp in samples:
            sp["source_url"] = sp.get("source_url", "")
            try:
                render_card(sp, cfg, out / f"{sp['id']}.jpg", date.today())
                res = f"kaynak={sp.get('photo_query') or '-'} · künye={sp.get('photo_credit') or '-'} · {sp.get('photo_url') or ''}"
            except NoOrgVisual:
                res = "**LOGO BULUNAMADI (ilan yayımlanmaz)**"
            lines.append(f"- {sp['id']} ({sp.get('organization') or sp['title_tr']}): {res}")
            if sp["visual_kind"] != "scene":
                lines += [f"    · {t}" for t in orgvisual.TRACE]
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
