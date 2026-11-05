# ATLAS FAST — Ortak WAL yazıcı kilidi, izole prototip

**Tarih:** 5 Kasım 2026  
**Konum:** `~/Atlas/ATLAS_FAST_DEV`  
**Statü:** Offline kanıt adayı. **NO-GO FOR LIVE.**

## Mevcut sorunun sınırları

`owner_single_execution.py` aktif geliştirme kopyasında `ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN` ile otomatik sıfır giriş WAL çözümünü kapalı tutuyor. Bu engel **kaldırılmadı**.

`wal_writer_fence_candidate.py` tüm yazıcıların ortak kullanabileceği yeni, ayrı bir deneysel API'dir. Canlı Owner SDK veya mevcut `run_once` yazıcılarına bağlanmadı.

## Aday mekanizma

- `writer_fence()`: Mac/POSIX `fcntl.flock(LOCK_EX|LOCK_NB)` ile aynı dizindeki sabit isimli kilit dosyasını dışlayıcı tutar. Kilit devredilemezse fail-closed ret verir. Symlink, dizin ve dosya izinleri, sahip UID, hardlink ve inode tutarlılığı denetlenir.
- `append_record()`: kilidi tutarken geçerli scope ve WAL kayıtlarını inceler; aynı scope daha önce çözümlenmişse yazmayı engeller. `O_EXCL|O_NOFOLLOW` ve `fsync` ile kayıt oluşturur.
- `resolve_reserve_only()`: yalnızca **ayrı doğrulayıcının sağladığı sentetik `proof=True`** kabulüyle tam rezervasyon bağlamını kontrol eder; aynı kilit altında yeniden WAL tarayıp başka ENTRY/incident yoksa `resolved` işaretini `O_EXCL/fsync` ile oluşturur.
- Bu prototipte `proof` sahte / açıkça verilen karar girişidir; Binance imzası, gelir defteri ve risk/STOP kanıtı burada doğrulanmaz. Dolayısıyla bu prototip tek başına güvenli üretim çözümü değildir.
- Kilidi tutan proses durursa işletim sisteminin kilidi serbest bırakması doğrulama mantığının yerine geçmez; sonraki işlem WAL durumunu yeniden denetler.

## Bağımsız proses yarış testleri

`tests/test_wal_writer_fence_candidate.py` gerçek alt süreçler kullanarak şunları test eder:

1. Başka proses kilidi tutarken yazma reddedilir.
2. Rezervasyon sonrası çözüm markerı yazıldıysa yeni ENTRY oluşturulamaz.
3. ENTRY önce yazılmışsa sonradan çözüm markerı oluşturulamaz.
4. Farklı iki proses aynı scope'a ENTRY yazmak için yarıştığında, çözüm yolu kayıt varsa engellenir.
5. Ayrı proseslerde bir resolver ve bir ENTRY yazıcısı eşzamanlı çalıştığında marker ve ENTRY **birlikte** kalamaz.
6. Bozuk WAL, yanlış UID/izin, dizin/kilit symlink, eksik kanıt, dosya adı hatası ve yinelenen kayıt reddedilir.

**Son toplam: 156/156 çevrimdışı test geçti.** Önceki 142 teste 14 yeni prototip testi eklenmiştir.

## Bitmemiş üretim bağlantısı

- Var olan `journal`, `write_policy_reserve_record`, `emergency_note`, STOP/TP/emergency yazarları ve `mark_scope_resolved` henüz ortak kilit prototipini çağırmıyor.
- `owner_single_trade.lock`, mutation lease ve emergency lock ile lock-order/deadlock testi yapılmadı.
- Restart okuyucularının, diğer yazarların, WAL dosya isimlerinin ve manuel inceleme prosedürünün sözleşmesi yeniden gözden geçirilmelidir.
- `proof=True` yerine gerçek `PolicyGate` kapanışı, kesin signed order-absence, imzalı FLAT ve tam scope ilişkisi güvenilir bir bağla doğrulanmalı; aşamalı ve fail-closed olmalı.
- İmzalı `query_order` read-only canlı karşılığı hâlâ `SenderLocked` nedeniyle doğrulanmadı.
- Aktif Owner SDK üzerine hiçbir kaynak yazılmadı; Binance emir veya iptal çağrısı yapılmadı.

**Karar: NO-GO.** Bu paket ortak kilidin bağımsız proses davranışını doğrular, fakat bütün Owner yazıcılarına uygulanmış tam sürüm değildir.
