# ATLAS FAST — WAL Yarışına Karşı Geçici Fail-Closed Koruma

**Tarih:** 5 Kasım 2026  
**Dal:** `dev/20261105-preflight-diagnostics`  
**Canlıya geçiş:** **NO-GO**

## Saptanan yarış

`_resolve_verified_zero_entry_scope`, son `_restart_wal_records()` okumasından hemen sonra kalıcı `resolved_<scope>.json` işareti yazabiliyordu. Başka bir WAL yazıcısı bu arada ENTRY veya incident kaydı üretirse yanlış çözümlenme işareti oluşması riski bulunur. Aynı kilidin bütün yazıcılarda kullanıldığı kanıtlanmadı.

İlk dokuzuncu paket testinde bu yarış 1 `expectedFailure` olarak tekrarlanmıştı. Bu testin normal geçirilmesi için yalnızca son işareti kilitlemek yeterli değildir.

## Uygulanan geçici önlem

Geliştirme kopyasında, bütün diğer kanıtlar sağlansa dahi `_resolve_verified_zero_entry_scope` sonunda `Blocked("ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN")` üretilir. `mark_scope_resolved` çağrılmaz. Kapanmış defter veya salt-okunur Binance emir-yokluğu kanıtı artık **tek başına otomatik serbest bırakma yaratmaz**.

Sonuç: kapalı risk defteri + açık rezervasyon WAL durumunda manuel inceleme ihtiyacı devam eder. Bu tasarlanmış emniyetli aksama, sistemin yeni işlem yapmasını engeller. İlgili testte çözüm işareti oluşturulmadığı doğrulanır.

**Önemli:** Bu durum TOCTOU probleminin tamamen giderildiği anlamına gelmez. Ortak kilit ve bütün yazıcıların atomik korunması hâlâ çözülmemiş geliştirme işidir.

## Eşzamanlılık çözümü için açık koşullar

1. `journal`, `write_policy_reserve_record`, `emergency_note`, `mark_scope_resolved` ve varsa bağımsız WAL yazıcılarının tamamı aynı güvenilir karşılıklı dışlama protokolüne katılmalıdır.
2. Son WAL doğrulaması ile çözüm işaretinin O_EXCL/fsync kalıcı yazımı tek kritik bölümde yapılmalıdır. Uzun süren borsa GET'leri kilit altında gereksiz yere bekletilmemeli; fakat kritik bölümde gerekirse kanıtlar yeniden kontrol edilmelidir.
3. Ortak kilit için uid, symlink, chmod, dosya tanıtıcısı, kilit edinme ve çökme davranışı fail-closed testlerden geçmelidir.
4. `owner_single_trade.lock` ve mutasyon fence ile lock-order/deadlock analizi yapılmalı; koruyucu STOP işlemlerini bloke edecek mekanizma eklenmemelidir.
5. Önceden gönderilmiş ama WAL'de bulunmayan emir ihtimalinin kesin salt-okunur sorguyla kapatılması gereklidir; gerçek Binance `OrderNotFound` davranışı `SenderLocked` nedeniyle hâlâ doğrulanmamıştır.
6. Gerçek atomik yazıcı-kilit senaryosu ayrı proseslerle sınanmadan otomatik çözüm tekrar etkinleştirilmemelidir.

## Test ve sürüm sınırları

`AtlasFastOwnerSDK/.venv/bin/python -B -m unittest discover -s tests -q`: **142/142 çevrimdışı test başarılı** (eski `expectedFailure` artık normal test olarak geçer). Bu başarı, yarışın *kilitlenerek* giderilmesi değil, güvensiz otomatik çözümün kapatılması kanıtıdır.

Aktif Owner SDK değiştirilmedi. Canlı emir, iptal, otomatik düzleştirme, güvenlik sınırı değiştirilmedi. RC1 etiketi değiştirilmedi; GitHub'a gönderim veya canlıya yükleme yapılmadı.
