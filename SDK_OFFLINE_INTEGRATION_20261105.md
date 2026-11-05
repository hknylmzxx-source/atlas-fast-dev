# ATLAS FAST — Paket 7: Binance SDK bağımlılık ve HTTP koruma sözleşmesi

**Tarih:** 5 Kasım 2026
**Ortam:** `~/Atlas/ATLAS_FAST_DEV`, dal `dev/20261105-preflight-diagnostics`.
**Kapsam:** yalnızca kurulu SDK Python paketlerini ve kaynak dosyalardan seçilmiş koruma işlevlerini çevrimdışı çalıştırma; canlı yürütme yok.

## Bağımlılıklar ve kurulu SDK metotları

Owner yürütücünün kodla sabitlenmiş bağımlılıklarıyla Mac'teki `AtlasFastOwnerSDK/.venv` sürümleri eşleşti:

- `binance-sdk-derivatives-trading-usds-futures==17.6.0`
- `binance-common==4.5.1`

Kurulu `DerivativesTradingUsdsFuturesRestAPI` nesnesinde `query_order`, `account_information_v3`, `current_all_open_orders` ve `current_all_algo_open_orders` metotları bulundu; `query_order` için `symbol`, `orig_client_order_id`, `recv_window` parametreleri doğrulandı.

## İzole HTTP istek koruması testi

`tests/test_sdk_transport_offline.py` doğrudan Owner kaynak kodunun `_order_not_found` ve `make_guarded_request` fonksiyonlarını AST üzerinden yükler. Kullanılan `original(request)` gerçek bir HTTP istemcisi **değildir**: her çağrı bir mock HTTP cevabına döner. Testlerde API anahtarı, Keychain, imzalı SDK nesnesi, Binance ağı, POST veya DELETE yoktur.

Testlenen davranışlar:

- Sadece tam izinli `GET https://fapi.binance.com/fapi/v1/order`, HTTP **400** ve JSON tamsayı hata kodu **-2013** halinde `OrderNotFound` sınıflandırması.
- HTTP 429/500, yanlış hata kodu ve güvenilmeyen yönlendirme **emir yok kanıtı sayılmaz**.
- HTTPS dışı sunucu, yanlış host, bilinmeyen GET yolu, izin verilmeyen HTTP yöntemi, silahlı olmayan POST ve DELETE çağrıları transport mock'una ulaşmadan engellenir.
- Yanıt yönlendirmesi kapalı tutulur.

## Sonuç / sınırlama

`'/Users/hakanyilmaz/Library/Application Support/AtlasFastOwnerSDK/.venv/bin/python' -B -m unittest discover -s tests -q`: **115 test geçti**, sıfır başarısızlık.

Gerçek imzalı `query_order` çağrısı `sdk_signed_sender.SenderLocked` ile **reddedildi**. Tekrar denenmedi veya güvenlik kilidi aşılmadı. Bu nedenle gerçek Binance'den `-2013` yanıtı alındığına ilişkin kanıt **yoktur**; ağsız mock sonucu gerçeğin kanıtı olarak yorumlanamaz.

Geliştirme dalı dışında hiçbir kod, risk limiti, WAL dosyası veya Binance emri değiştirilmedi. Canlı dağıtım yetkisi verilmedi.

**Sonraki teknik kontrol:** yalnızca izinli, sahibi tarafından yetkilendirilmiş read-only entegrasyon yolunda `OrderNotFound` davranışının gerçek borsa yanıtıyla gözlenmesi; yapılamıyorsa bu madde ayrı bir blocker olarak tutulacaktır.
