# ATLAS FAST — WAL Çözüm İşareti Yollarının Kaynak Denetimi

**5 Kasım 2026.** Yalnızca `ATLAS_FAST_DEV` üzerinde statik denetim; **NO-GO**.

## Kapsam

`wal_marker_static_audit.py`, `owner_single_execution.py` kaynağını yalnız AST olarak ayrıştırır; gerçek yürütücüyü import/execute etmez, Binance SDK, ağ, anahtar veya gerçek emir kullanmaz.

Çözüm işaretini oluşturan üç çağıran saptandı:

1. `risk_ledger_entry_gate`: rezervasyon reddi sonrasında risk defterinde bekleyen scope olmadığı düşünülerek `mark_scope_resolved(..., "RESERVATION_REFUSED_NO_LEDGER_CHANGE")` kullanılır.
2. `_restart_one`: pozisyon ve emirlerin FLAT okunduğu, settle başarılı olduğu bazı yeniden başlatma akışlarında `mark_scope_resolved(..., "RESTART_SETTLED_SIGNED_FLAT")` kullanılır.
3. `run_once`: devamlı STOP doğrulaması, FLAT ve çıkış temizliği şartlarının ardından `mark_scope_resolved(..., "FLAT_AND_OWNED_EXITS_VERIFIED_CLEARED")` kullanılır.

Ayrıca `_resolve_verified_zero_entry_scope` otomatik marker çağrısı hâlâ kaldırılmıştır. Ortak `writer_fence` ve ayrı `emergency_restart_diagnostic_candidate` üretim yürütücüsüne import edilmemiştir. `startup_reconcile` çözülmüş scope kümesini işaretlerden çıkarır; çözüm sonrası ek WAL kayıtlarını **dosya listesinin atomik anlık görüntüsüne bağlayan** bir ortak kilit kullanılmaz.

Bu bulgular, tek bir zero-entry auto-resolve kapatmasının bütün marker üretim yollarını kapsamadığını gösterir. 3 mevcut marker çağıranının tamamında risk ayrıca incelenmelidir. Kaynak denetimi, doğrudan yarışın hepsini kanıtladığı anlamına gelmez, ama kilitsiz yayın noktalarını gösterir.

## Testler

`tests/test_wal_marker_static_audit.py`: 7 yeni test, marker çağıranlarının değişmez listesi, yeni çağıran olursa yakalama, otomatik sıfır-entry çözümünün devre dışı kalması, henüz bağlanmamış fence/alarm modülleri ve NO-GO sınıflandırması.

**Toplam 211/211 çevrimdışı test başarılı** (204+7).

## Entegrasyon önceliği

- Üç marker çağıranından en az biri inceleme dışında kalırsa genel WAL çözümü güvenli sayılmamalıdır.
- Yeni yazarlar/incident, STOP/TP ve karantina kayıtları ile birlikte **aynı doğrulama sınırına** alınmalıdır; ortak kilit acil alarmın kaybolmasına yol açmamalıdır.
- Salt dosya varlığı ve ayrı zamanlarda yapılan iki okuma gerçek ENTRY atomikliğine kanıt değildir.
- Mevcut aktif Owner SDK ve RC1 etiketi değişmedi; otomatik WAL çözümü kapalı; Binance emir yazımı yapılmadı.

**Canlı karar: NO-GO.**
