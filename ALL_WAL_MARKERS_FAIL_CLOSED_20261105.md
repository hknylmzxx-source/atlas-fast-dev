# ATLAS FAST — Tüm marker yazma yollarında geçici fail-closed koruma

**5 Kasım 2026. Geliştirme alanı:** `~/Atlas/ATLAS_FAST_DEV`. **Canlı karar: NO-GO**.

## Sorun

Statik kaynak denetimi `mark_scope_resolved` çağrısını üç farklı akışta bulmuştu: `risk_ledger_entry_gate`, `_restart_one`, `run_once`. Önceki değişiklik yalnızca sıfır-ENTRY çözümünü kapatıyordu. Diğer üç akış, bütün WAL yazıcıları ve kritik olay kaydı arasında doğrulanmış atomik kilit yokken çözüm işareti oluşturabiliyordu.

## Geliştirme kopyasındaki önlem

`owner_single_execution.py` içindeki `mark_scope_resolved` artık dosya oluşturmaz veya kalıcı marker yazmaz. Her çağrıda `Blocked("WAL_RESOLUTION_MARKER_DISABLED_SHARED_FENCE_UNPROVEN")` üretir. Bu yöntem eski marker kayıtlarını silmez ve mevcut çözülmemiş WAL kayıtlarını açık tutar. Tek bir ortak fonksiyon kullanıldığı için üç çağıranın tamamı etkilenir.

`tests/test_safety_chain_integration.py` içindeki simüle edilmiş FLAT/settle senaryosu, marker yazmaya çalışmanın engellendiğini, marker dosyası oluşmadığını, başlangıç kontrolünün hâlâ blok verdiğini ve yeni işlem izni açılmadığını doğrular.

`tests/test_wal_marker_static_audit.py` içine iki regresyon eklendi: üç marker çağıranının tek engelleyici fonksiyona bağlı kalması ve gerçek fonksiyon gövdesinin yalnız izole AST derlemesiyle çalıştırıldığında dosya yazmadan reddetmesi.

**Toplam: 213/213 çevrimdışı test başarılı** (211 + 2).

## Kısıtlar

- Bu bir nihai atomik kilit düzeltmesi **değildir**; güvenli tarafta kapanma karşılığında bazı scope'ların otomatik çözümlenmesini engeller.
- Geliştirme kopyasındaki bu davranış canlı Owner SDK'ya aktarılmadı. Gerçek uygulamanın bütün marker yolları henüz değiştirilmiş değildir.
- Ortak WAL kilidi, emergency latch, STOP/TP zamanlaması ve gerçek signed `query_order` kanıtı hâlâ ayrı tamamlanması gereken işlerdir.
- RC1 etiketi, canlı emir yazma izni, active SDK ve Binance hesap durumu bu paket ile değiştirilmedi.

**Canlıya geçiş: NO-GO.**
