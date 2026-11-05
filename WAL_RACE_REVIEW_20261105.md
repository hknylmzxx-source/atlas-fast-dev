# ATLAS FAST — Paket 9: WAL yarış ve belirsiz ENTRY incelemesi

**Tarih:** 5 Kasım 2026
**Yer:** `~/Atlas/ATLAS_FAST_DEV`
**Dal:** `dev/20261105-preflight-diagnostics`
**Durum:** **OFFLINE / NO-GO FOR LIVE**

## İyileştirilen hata: yeniden başlatma giriş günlüğünü yanlış yorumluyordu

Önceki `_restart_entry_fill()`, hedef scope için `ENTRY_<hash>.json` yoksa doğrudan `"0"` döndürüyordu. Dosyanın yokluğu, ağ belirsizliği veya beklenmedik kapanma halinde Binance'e hiçbir emir ulaşmadığının bağımsız kanıtı değildir.

Yeni geliştirme davranışı:

- ENTRY WAL kaydı yoksa, `query_order` ile **tam symbol + clientOrderId için imzalı GET** beklenir.
- Yalnızca kesin, dar `OrderNotFound` istisnasında `"0"` kabul edilir.
- GET hata/zaman aşımı, emir bulunması veya terminal 0-fill dahi olsa WAL yokluğu durumunda `None` ile fail-closed kalır; yeniden başlatma `KEPT_ENTRY_STATUS_UNKNOWN` döndürür.
- Reserve-only scope `"0"` ile defterini kapatsa bile restart çözüm işareti, `_resolve_verified_zero_entry_scope()` adlı ek WAL/gelir/FLAT/defter kanıt fonksiyonundan geçmeden yazılmaz.
- **Hiçbir POST/DELETE** veya canlı dosya erişimi eklenmedi.

`tests/test_restart_exact_order_evidence.py` dokuz yeni offline senaryoyu kapsıyor.

## Kritik açık — WAL son okuma / resolved işareti TOCTOU yarışı

`_resolve_verified_zero_entry_scope()` içinde:

1. WAL A anında okunur.
2. Binance yokluk, risk defteri ve FLAT kanıtı kontrol edilir.
3. WAL B anında okunur ve A ile karşılaştırılır.
4. `mark_scope_resolved()` çağrılır.

**B ile 4. adım arasında** başka bir yazıcı yeni ENTRY kaydı ekleyebilir. İkinci WAL okuması başarılı olmuş olsa bile çözümlenme işareti yeni ENTRY kaydı ile birlikte kalabilir. `startup_reconcile`, aynı scope için `resolved` görünce yeni kayıt hakkında ayrı değerlendirme yapmadan scope'u temiz kabul edebilir.

`tests/test_safety_chain_integration.py::test_known_race_between_final_wal_scan_and_resolution_marker` testi bu durumu sentetik olarak **yeniden üretmektedir**. Güvenlik beklentisi (marker oluşturulmaması) mevcut kodda sağlanmadığı için test `@unittest.expectedFailure` olarak tutuldu. Bu işaret hata kapatıldı demek değildir; sonuçlarda **1 beklenen başarısızlık** olarak ayrı görünür.

## Test özeti

`AtlasFastOwnerSDK/.venv/bin/python -B -m unittest discover -s tests -q`:

- **142 test çalıştırıldı.**
- **141 başarılı.**
- **1 beklenen başarısızlık** (WAL atomiklik yarışı).
- İlgili güvenlik açığı **düzeltilmedi**. Koddan onay/koruma kaldırılmadı.

## Güvenli çözüm için sonraki tasarım şartı

- Tek bir owner-özel **ortak dışlayıcı kilit / write-fence** tüm WAL ENTRY ve reserve/incident yazıcılarıyla çözüm işareti yazıcısını kapsamalı; yalnız marker yazan işlevi kilitlemek yeterli değildir.
- Kilit kanıtı, jurnalın kaynak UID/izinleri ve atomik dosya işlemleriyle birlikte ayrı simülasyonda doğrulanmalı.
- Kilit devredilemez/izinsizse `resolved` yazma **fail-closed** olmalı.
- Eşzamanlı yazıcı ile otomatik çözüm yarışının gerçekten engellendiğini kanıtlayan test, beklenen başarısızlıktan normal başarılı teste dönmeden canlıya geçilmemeli.
- Ayrıca gerçek Binance `query_order -2013` imzalı yanıtı `SenderLocked` nedeniyle hâlâ kanıtlanamadı.

**Karar:** **NO-GO FOR LIVE**; bu paket bir güvenlik incelemesi ve seçilmiş restart fail-closed düzeltmesidir. Yerel Git RC1 etiketi değiştirilmemiştir.
