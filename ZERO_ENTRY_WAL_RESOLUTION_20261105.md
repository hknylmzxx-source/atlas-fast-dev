# ATLAS FAST — Paket 6: Sıfır giriş WAL çözümü (OFFLINE ADAY)

**Uygulama:** `~/Atlas/ATLAS_FAST_DEV`, `dev/20261105-preflight-diagnostics`.

**Durum:** Sadece izole Git geliştirme kopyası. Üretim Owner SDK'ya, gerçek emir göndermeye, STOP koruma ve risk limitlerine uygulanmamıştır.

## Çözülen kök sebep

`policy_settle_after_flat(gate, ..., entry_filled_qty="0")` gelir geçmişiyle imzalı FLAT kanıtını birleştirip risk defterinde güvenilir bir `CLOSED` kaydı üretebiliyordu. Ancak `POLICY_RESERVE_<scope>.json` WAL kaydı için `resolved_<scope>.json` işareti oluşmuyordu. Yeniden başlatma, haklı olarak çözülmemiş WAL gördüğü için yeni ENTRY'yi reddediyordu.

## Kanıt zinciri

`_settle_zero_entry_and_resolve()` yalnızca iki kesin gönderim-öncesi reddi yolundan çağrılır:
1. Owner onayı, risk rezervasyonundan sonra süresi dolmuşsa.
2. `one_write("ENTRY")`, POST öncesinde `PreSendRefused` üretmişse.

Yürütücünün **bilinmeyen emir sonucu**, imzalı geri okuma belirsizliği veya emir POST'undan sonraki hiçbir yol otomatik çözüm adayına dahil değildir.

`_resolve_verified_zero_entry_scope()` aşağıdaki koşullarda **ayrı bir `resolved_<scope>.json`** işareti yazabilir; şartlardan biri eksikse yazmaz:

- `policy_settle_after_flat(..., "0")` başarılı olmalı; signed income/FLAT gelir kontrolü önceki `PolicyGate.settle` sözleşmesinde zaten yapılmış olmalı.
- Kapanış olayı `CLOSED`, tam `scope`, `SIGNED_INCOME_REALIZED_PNL` kaynağı ve **tam sıfır** `realized_pnl_usdt` içermeli. Defter, `gate._scope_events()` ile yeniden okunarak kapanış ve açık rezervasyon kalmaması doğrulanmalı.
- `POLICY_RESERVE_<scope>.json` tek scope-journal olmalı; `intent`, `scope`, `symbol` ve `client_id` planlanan ENTRY ile birebir eşleşmeli. ENTRY/STOP/TP ve incident kaydı varsa otomatik çözüm engellenmeli.
- Binance `query_order` tam `orig_client_order_id` için yalnızca imzalı **HTTP 400 / hata kodu -2013** (`OrderNotFound`) döndürmeli. Sıradan timeout, başka hata veya kayıtlı emir kabul edilmez.
- Defter kapanışından **sonra** yeni imzalı account/normal/algo snapshot alınmalı. Tüm sembollerde pozisyon sıfır ve tüm açık emir listeleri boş olmalı.
- WAL ikinci kez okunmalı; kanıt kontrolü sırasında değişmişse otomatik çözüm engellenmeli.
- Son olarak mevcut `mark_scope_resolved()` ile O_EXCL/fsync kullanılarak kalıcı işaret yazılır. Hiçbir orijinal WAL dosyası silinmez.

## Test sonuçları

`python3 -B -m unittest discover -s tests -q`

**101/101** çevrimdışı test. Önceki 72 teste ek olarak 29 yeni test; bunlar kesin pre-send reddi ve aşağıdaki negatif durumları kapsar: bulunan emir, GET timeout, açık STOP, başka sembolde pozisyon, tutarsız CLIENT ID, ENTRY/STOP WAL varlığı, incident, yanlışa çevrilmiş defter kaynağı/PNL, eksik CLOSED, WAL bozulması veya kontrol sırasında değişmesi, bilinmeyen POST sonucu, ikinci kez çözüm.

Ayrıca `tests/test_binance_order_absence_contract.py`, AST'den gerçek `_order_not_found` sınıflandırmasını alarak 10 sentetik HTTP testi çalıştırır: yalnızca GET `/fapi/v1/order`, HTTP 400 ve JSON tamsayı `-2013` kabul edilir; 429/500, başka kod, bozuk JSON, başka metod/yol kabul edilmez.\n\nÖnemli: Bu testler gerçek Binance'in `-2013` yanıtını taklit eder; üretimdeki SDK sürümü ve ağ koşullarıyla gerçek `OrderNotFound` çevriminin doğrulanması ayrı bir salt-okunur entegrasyon aşamasıdır.

## Risk sınırları / dağıtıma hazır olma

- Bu çözüm **yalnızca ön-gönderim sıfır gerçekleşme** için adaydır; normal STOP/TP kapanışlarını değiştirmez.
- Tam `run_once()` entegrasyonunu ve gerçek Binance bağlantısını çalıştırmadık.
- Kaydın yazıldığı andan sonraki yeni piyasa değişimleri için mutlak garanti iddiası yoktur. Hedef: terminal kapanış kanıtına rağmen gereksiz kalan rezervasyon WAL'sını güvenli sınırlar içinde çözmek.
- Günlük değişikliği, canlı emri, risk parametresi değişikliği, Binance POST/DELETE veya GitHub push yapılmamıştır.
- Canlı dağıtım ve owner onayı hâlâ ayrı bir aşamadır.
