# ATLAS FAST — Yalıtılmış geliştirme alanı

Bu depo yalnızca **offline geliştirme** içindir. Mac üzerindeki aktif Owner SDK'ya bağlı değildir; emir gönderilmez ve bu klasördeki yürütücü çalıştırılmaz. Yalnızca dört seçilmiş Python kaynak dosyası, başlangıç SHA-256 manifesti ve testler bulunur. Binance anahtarı, canlı izin, risk defteri veya emir günlüğü kopyalanmaz.

## İlk geliştirme paketi

- `order_filters.py`: 300.000 ms (5 dakika) exchangeInfo alım zamanı sınırı değiştirilmedi. Eski veri ve gelecekte görünen alım zamanı artık ayrı hata kodlarıyla reddedilir.
- `owner_single_execution.py`: hata kodları mevcut `MAX_RISK=0.75` ve `MAX_LEVERAGE=3` sabitlerinden türetilir; limitler **değiştirilmez**.
- `order_filters.py`: filtre risk ret mesajı geçirilen üst sınırdan üretilir. Belgelerdeki eski 1x/100/0.30 bilgileri mevcut 3x/285/2.70 filtre tavanlarıyla eşleştirildi. Owner yürütücüdeki 0.75 limiti ayrı, daha sıkı bir kapıdır.

Test: `python3 -B -m unittest discover -s tests -v`

## İkinci geliştirme paketi — piyasa verisi gecikmesi

Altı adet geçmiş `EXCHANGE_INFO_STALE_OR_CLOCK_MISMATCH` reddi aynı `run_once` preflight satırında görüldü. Eski loglarda gerçek süre ölçümü bulunmadığından kaynağı kesin olarak belirlenemedi.

- **İlk preflight ve rezervasyon öncesi:** `signed_income_envelope()` önce, `policy_market()` sonra alınır; böylece olası yavaş gelir sorgusu eski market snapshot'ını bekletmez.
- **Fail-closed:** `PolicyGate` yine kaynak güncelliğini, fiyat mark zamanını, gelir belgesi doğruluğunu ve risk bütçesini kontrol eder. Eski bilgiye tolerans, güvenli olmayan yenileme veya yeniden emir denemesi yoktur.
- **Teşhis:** Ret halinde millisecond ölçüleri (`INCOME`, `MARKET`, `INFO_AGE`, `MARK_AGE`) hata mesajına eklenir. `EXCHANGE_INFO_TOO_OLD` ve saat uyuşmazlığı hataları tam yaş/fark değerini bildirir.
- **Sınır:** Bu simülasyon, Binance SDK'sının gerçek ağ gecikmesini ölçmez. Gelir belgesi sorgusunun kendisi hata verirse helper güvenli biçimde başarısız olur; aşama sonu metrikleri üretilemez. Gerçek kök neden için daha sonra salt-okunur entegrasyon testleri gerekir.

Çevrimdışı regresyon testleri: `python3 -B -m unittest discover -s tests -v`.

## Güvenlik ve sınırlar

Testler saf fonksiyonları ve yürütücünün AST'den **yalnız risk ve piyasa-verisi toplama fonksiyonlarını** sahte borsa yanıtlarıyla çalıştırır. Owner uygulaması, ağ bağlantısı, şifre, imzalı GET veya gerçek POST/DELETE testi yapılmaz. Tüm entegre testler ve canlıya alma ayrı inceleme gerektirir.

Bilinen sözleşme borcu: `order_filters.py` içindeki `margin_1x_usdt` çıktı adı eskidir; değer aslında kaldıraçla bölünmüş marjindir. Olası tüketicileri bozmamak için ilk pakette alan adı değiştirilmedi.

Kaynak dosyalar `SOURCE_SNAPSHOT.json` içindeki SHA-256 özetleri ile Mac'in aktif Owner SDK'sından yalnızca okunarak alınmıştır. Bu dalın GitHub'a gönderimi yapılmamıştır.
