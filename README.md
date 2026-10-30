# Diploport Lite

Türk gençleri için burs, staj, gönüllülük ve eğitim fırsatlarını **otomatik** tarar, Türkiye'den başvurulabilir olanları seçer, Instagram'a ve web sitesine koyar. Hedef: minimum maliyet, minimum parça.

```
GitHub Actions (ücretsiz, her 3 saatte bir)
 ├─ 1. 7 RSS kaynağını doğrudan oku, görülenleri/eskileri kodla ele
 ├─ 2. İlanın sayfasını aç → temiz metin + sayfadaki GERÇEK harici linkler
 ├─ 3. Tek Gemini çağrısı → uygunluk + kanıt cümlesi + Türkçe özet + başvuru linki SEÇİMİ
 │      kod: kanıt sayfada var mı? link çalışıyor mu? aggregator'a mı çıkıyor? tekrar mı?
 ├─ 4. Kesin uygun → kuyruk · Belirsiz → Telegram'da Yayınla/Yayınlama düğmesi · Uygun değil → elenir (nedeniyle)
 ├─ 5. Kotaya göre günde en fazla 2 ilan: Diploport şablonuyla 1080×1350 JPEG kart üret.
 │      Arka plan: Gemini'nin ilana özel verdiği İngilizce sorgularla Unsplash'tan (yedek: Pexels) otomatik stok fotoğraf
 │      (sorgu sonuçsuzsa kategoriye göre genel arama; hiçbiri yoksa şablonun düz hâli)
 ├─ 6. Siteye ekle (docs/posts.json) → GitHub Pages
 └─ 7. Görsel yayına girince Make webhook'u → Instagram'a paylaş
```

## Maliyet

| Parça | Aylık |
|---|---|
| GitHub Actions + Pages (herkese açık depo) | 0 |
| Gemini 3.1 Flash-Lite (Google'ın ücretsiz katmanı) | 0 |
| Make (ücretsiz plan, post başına ~3 kredi → ~150/1000) | 0 |
| Unsplash API (ücretsiz anahtar, saatte 50 istek; biz günde ~2 kullanıyoruz) ya da Pexels | 0 |
| Telegram botu | 0 |
| Alan adı (isteğe bağlı) | ~10 $/yıl |

Gemini ücretsiz katmandan çıkarsa en kötü senaryo (günde 80 ilan okunur) yaklaşık 4 USD/ay; gerçekçi kullanım 1-2 USD. Ücretsiz katmanda gönderilen veriler Google tarafından ürün geliştirmede kullanılabilir; burada işlenenler zaten herkese açık ilan metinleri.

## Kurulum (yaklaşık 30-40 dakika, bir kez)

### 1. GitHub
1. Yeni **herkese açık (public)** bir depo aç (ör. `diploport`) ve bu klasörün içeriğini yükle. Public olması Pages ve Actions'ın ücretsiz olması için gerekli.
2. `config.yaml` içinde `brand.site_url` (örn. `https://KULLANICI.github.io/diploport`) ve `brand.handle` alanlarını düzenle.
3. Settings → Pages → *Deploy from a branch* → Branch: `main`, klasör: `/docs`.
4. Settings → Actions → General → Workflow permissions → **Read and write permissions**.

### 2. Gemini anahtarı
aistudio.google.com → *Get API key*. Anahtarı depoda Settings → Secrets and variables → Actions → **`GEMINI_API_KEY`** olarak ekle. (Ücretsiz katman limitleri AI Studio'da hesabına özel görünür; kod 429'larda kendiliğinden bekleyip dener. Ana model "yoğun talep" (503) verirse `config.yaml > gemini.fallback_models` altındaki yedek modellere sırayla geçer; yoğun çıkan model 10 dakika atlanır.)

### 3. Stok fotoğraf anahtarı (kart arka planı için)
unsplash.com/oauth/applications → *New Application* (ücretsiz, hesap gerekir) → **Access Key**'i depoda **`UNSPLASH_ACCESS_KEY`** secret'ı olarak ekle. (Pexels şu an yeni anahtar vermiyor; eski anahtarın varsa `PEXELS_API_KEY` olarak eklenebilir, Unsplash çalışmazsa yedek olarak kullanılır.) Anahtar yoksa kartlar fotoğrafsız, şablonun beyaz zeminli hâliyle çıkar (hat durmaz). Kullanım yönergesi gereği fotoğrafın sahibi açıklamaya "Görsel: İsim / Unsplash" olarak eklenir. Unsplash "demo" modunda saatte 50 istek verir; günde 1-2 ilan için fazlasıyla yeter.

### 3b. Telegram (onay mesajları için)
1. Telegram'da `@BotFather` → `/newbot` → verdiği token'ı **`TELEGRAM_BOT_TOKEN`** secret'ı yap.
2. Yeni botuna `/start` yaz, sonra tarayıcıda `https://api.telegram.org/bot<TOKEN>/getUpdates` aç; `"chat":{"id":...}` değerini **`TELEGRAM_CHAT_ID`** secret'ı yap.
Telegram ayarlanmazsa "belirsiz" ilanlar onaya düşmek yerine elenir (nedeni raporda görünür).

### 4. Make (sadece Instagram'a gönderen küçük senaryo)
Yeni bir senaryo, **2 modül**:
1. **Webhooks → Custom webhook** (yeni webhook oluştur, adresi kopyala → **`MAKE_WEBHOOK_URL`** secret'ı). "Run once" ile bir kez çalıştırıp GitHub tarafından ilk veri gelince yapıyı tanıt.
2. **Instagram for Business → Create a Photo Post** (mevcut Facebook bağlantını seç): *Photo URL* = `image_url`, *Caption* = `caption`.
İki modül arasına filtre koy: `secret` = rastgele uzun bir metin; aynı metni **`MAKE_SECRET`** secret'ı yap (başkası webhook'unu tetikleyemesin). Senaryoyu aç (ON), hata bildirimleri açık kalsın.

### 5. İlk çalıştırma
Actions sekmesi → **Diploport** → *Run workflow* → `dry_run` işaretli. Bitince `data/last_run.md` dosyasını aç: hangi ilan neden elendi/kuyruğa girdi/onaya düştü, kanıt cümlesiyle birlikte görünür. Karardan memnunsan `dry_run` olmadan tekrar çalıştır; sonrası otomatik.

## Gündelik kullanım
- **Hiçbir şey yapman gerekmiyor.** Belirsiz ilan olursa Telegram'a başlık, özet ve linkle bir mesaj gelir; altında Yayınla / Yayınlama düğmeleri var. Karar, bir sonraki çalışmada (en fazla 3 saat) işlenir.
- Neden elendiğini görmek için `data/last_run.md` (son çalışma) ve `data/state.json` (`seen` altında her ilanın durumu ve nedeni).
- Kaynak eklemek/çıkarmak, günlük paylaşım sayısı, saat penceresi, hashtag'ler: sadece `config.yaml`.

## Eski sistemdeki sorunlar nasıl çözüldü?

| Sorun | Çözüm |
|---|---|
| Başvuru linki olarak aggregator sayfası paylaşılıyordu | Model link **yazmaz**; sayfadaki numaralı gerçek linklerden seçer. Kod linki açar, yönlendirmeyi takip eder, aggregator/sosyal medya alan adına düşüyorsa reddeder, izleme parametrelerini temizler. Resmî link yoksa ilan **paylaşılmaz** (sadece SALTO ve Youthall gibi başvurunun zaten kendi sayfasında yapıldığı kaynaklarda kaynak sayfası kullanılır). |
| Türklere kapalı ilanlar paylaşılıyordu | "Uygun" demek için sayfadan birebir kanıt cümlesi gerekir; kod bu cümlenin sayfada gerçekten geçtiğini doğrular. Uydurma kanıt → "belirsiz"e düşer. |
| Gereksiz eleme | Metnin tamamı (baş + son bölüm) modele gider; belirsizler çöpe değil, onaya gider; her eleme nedeniyle kaydedilir. |
| Görsel üretimi | Sizin Diploport şablonunuz (logo, lacivert gradyan, başlık, "Ülke \| destek", site adı) kodla çizilir. **Yurt dışı etkinlik/burs gibi "sahne" ilanlarında** arkasına Unsplash/Pexels'ten yeterince büyük (≥1080×1350) dikey stok fotoğraf gelir (aramayı model yapar: şehir → ülke+tema → genel sahne). **Belirli bir kuruma/şirkete özel ilanlarda** (kamu ilanı, şirket stajı...) stok fotoğraf konmaz; kartın arkasında **kurumun logosu** durur. Logo sırayla aranır: `assets/org_logos/` (elle) → Youthall ilan sayfası → Vikipedi → Wikidata → kurumun kendi web sitesi (site, sayfa başlığında kurum adı geçiyorsa kabul edilir). Logo bulunamazsa ilan yayımlanmaz, kuyrukta bekler ve Telegram'dan haber verilir; logoyu `assets/org_logos/<kurum-adı>.png` olarak eklemeniz yeter. 4:5 JPEG ve Türkçe karakterler her seferinde garanti. |
| Karmaşıklık/maliyet | ~12 modüllü zincir yerine tek kod akışı; Make sadece 2 modülle Instagram'a gönderir. |

## Bilmen gerekenler
- Sahne ilanlarındaki stok fotoğraf eşleşmesi otomatiktir; çok özel konularda (küçük bir şehir gibi) genel bir fotoğraf gelebilir. Fotoğrafın hangi aramayla bulunduğu `data/state.json` içinde (`outbox` / `published` kaydında `photo_query`) görünür; beğenmediğiniz bir eşleşme görürseniz `config.yaml > photo` altındaki genel sorguları ve parlaklık eşiğini ayarlayabilirsiniz.
- Instagram açıklamalarındaki linkler **tıklanabilir değildir**. Görselde ve açıklamada "başvuru linki açıklamada" yazar; link kopyalanabilir ve siteyle birlikte biyografiye de koyabilirsin.
- Kaynak sitelerin içerikleri özetlenip Türkçeleştirilir; ilan kaynağı açıklamada ve sitede belirtilir, başvuru resmî sayfaya yönlendirir.
- GitHub, 60 gün hareketsiz depolarda zamanlanmış çalıştırmaları durdurabilir; sistem düzenli commit attığı için normalde olmaz. Durursa Actions sekmesinden yeniden etkinleştir.
- Bir RSS kaynağı yayından kalkarsa o kaynak sessizce atlanır (Actions günlüğünde uyarı olur); diğerleri çalışmaya devam eder.
- Başvurmadan önce kullanıcılar şartları resmî sayfada doğrulamalı; sistem yapay zekâ + kural kontrolüdür, hatasız garanti etmez. Belirsiz olanları sen onaylarsın.

## Geliştirme
```
pip install -r requirements.txt pytest && python -m playwright install chromium
python -m pytest tests          # ~50 test, ağ gerektirmez
python -m diploport sample out.jpg [foto.jpg]   # tasarım önizleme (foto verilmezse Pexels/boş şablon)
```
