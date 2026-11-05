# ATLAS FAST — Canlıya Geçiş Öncesi GO / NO-GO Kontrol Listesi

**Aday:** `atlas-fast-v0.1.0-rc1-offline-20261105`  
**Tarih:** 5 Kasım 2026  
**Karar:** **NO-GO (CANLIYA GEÇİLMEYECEK)**

Bu liste testlerin gerçek kapsamını ve üretime geçmek için eksik kanıtları birbirinden ayırır. Tamamlanmış bir kontrol, canlı yürütme yetkisi vermez.

## Çevrimdışı doğrulananlar

- [x] Yedi geliştirme paketi ayrı `ATLAS_FAST_DEV` Git dalında kaydedildi.
- [x] Kurulu SDK sürümleri kodla eşleşiyor: Futures `17.6.0`, common `4.5.1`.
- [x] İmzalı GET metotlarının kurulu Python SDK arayüz imzaları doğrulandı.
- [x] `115/115` yerel çevrimdışı test geçiyor; STOP/TP, WAL, onay süresi, mutasyon belirsizliği ve yeniden başlatma testleri mevcut.
- [x] Seçilen aktif Owner SDK kaynakları SHA-256 ile eşleşiyor; aktif üretim kodu değiştirilmedi.
- [x] `git diff --check` temiz.
- [x] Yalnızca geliştirme kopyasına yeni kaynak/test/doküman yazıldı; Binance POST/DELETE yok.

## Üretim izninden önce kapanması gereken engeller

- [ ] **İmzalı read-only emir yokluğu kanıtı:** Gerçek `query_order` çağrısının, tam clientOrderId için doğru `-2013` davranışı güvenlik açısından izinli bir yolda doğrulanmalı. `SenderLocked` kilidi korunmalı; bypass yasak.
- [ ] **Sıfır-ENTRY WAL çözümü için bağımsız kod incelemesi:** Defter kaynağı, tam WAL kimlik bağlaması, anlık imzalı FLAT, tüm açık emirler, WAL yarış kontrolü ve O_EXCL/fsync kanıt zinciri gözden geçirilmeli.
- [ ] **Tam yürütücü entegrasyonu:** `run_once` seviyesinde bağımlılık enjeksiyonlu simülasyon; özellikle onay süresi, rezervasyon, giriş belirsizliği, STOP kaybı, kapanış/temizlik, yeniden başlatma.
- [ ] **Kaynak ve hata sözleşmeleri:** Gerçek SDK istisnalarının yüzeydeki `Blocked` / `OrderNotFound` haritası; bağlantı kesintisi, zaman kayması, 429/500 ve geciken income testleri.
- [ ] **STOP korumasının sürekliliği:** `continuous_stop_coverage_verified=true` için güvenilir ölçüm ve boşluk halinde fail-closed davranış; yalnızca tek anlık snapshot yeterli değil.
- [ ] **Acil durum kanalı/operasyon planı:** `emergency_flatten_connected=false` kaydı için kabul edilebilir müdahale planı ve yetkili operasyon incelemesi; izin olmadan emir kanalı açılmamalı.
- [ ] **Yayın/rollback planı:** Sürüm hash'i, paketleme, geri dönüş, gözlem/alarmlar, canlı SDK sürümü ve konfigürasyon sınırları, değişiklik onayı.
- [ ] **Bağımsız finansal mutabakat:** Manuel AINUSDT BNB komisyonunun dönüştürme kanıtı açık; raporlamada belirsizlik şeffaf tutulmalı.
- [ ] **Açık owner yetkisi:** Gerçek canlı emir ya da dağıtım ayrı ve açık yetki olmadan başlatılmayacak; mevcut `live_release_allowed=false` korunacak.

## Durdurma koşulları

Aşağıdaki durumlardan herhangi biri olursa **NO-GO** sürer: güvenlik kilidi, emre ilişkin belirsiz POST sonucu, açık pozisyona karşı doğrulanamayan STOP, bozuk veya değişen WAL, güncel olmayan imzalı okuma, çözülmemiş risk defteri, yeni emir/pozisyon gözlenmesi, test başarısızlığı veya sürüm hash uyumsuzluğu.

## Okuma-anı güvenlik notu

5 Kasım 2026 12:47:48 Türkiye saati gözlemci raporunda `FLAT_SIGNED_GET_ONLY`, 0 pozisyon / 0 normal emir / 0 algo emir vardı; `live_release_allowed=false`, `continuous_stop_coverage_verified=false`, `emergency_flatten_connected=false`. Bu geçmiş bir **anlık okuma**dır; sürekli durum veya gelecekte de FLAT olacağı iddiası değildir.

## Karar yetkisi

**Bu kontrol listesi hiçbir kilidi açmaz ve otomatik izin belgesi değildir.** Eksikler kapatılıp test/inceleme tutanakları sağlandıktan sonra ayrıca karar verilir.
