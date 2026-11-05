# ATLAS FAST — Yalıtılmış geliştirme alanı

Bu depo yalnızca **offline geliştirme** içindir. Mac üzerindeki aktif Owner SDK'ya bağlı değildir; emir gönderilmez ve bu klasördeki yürütücü çalıştırılmaz. Yalnızca dört seçilmiş Python kaynak dosyası, başlangıç SHA-256 manifesti ve testler bulunur. Binance anahtarı, canlı izin, risk defteri veya emir günlüğü kopyalanmaz.

## İlk geliştirme paketi

- `order_filters.py`: 300.000 ms (5 dakika) exchangeInfo alım zamanı sınırı değiştirilmedi. Eski veri ve gelecekte görünen alım zamanı artık ayrı hata kodlarıyla reddedilir.
- `owner_single_execution.py`: hata kodları mevcut `MAX_RISK=0.75` ve `MAX_LEVERAGE=3` sabitlerinden türetilir; limitler **değiştirilmez**.
- `order_filters.py`: filtre risk ret mesajı geçirilen üst sınırdan üretilir. Belgelerdeki eski 1x/100/0.30 bilgileri mevcut 3x/285/2.70 filtre tavanlarıyla eşleştirildi. Owner yürütücüdeki 0.75 limiti ayrı, daha sıkı bir kapıdır.

Test: `python3 -B -m unittest discover -s tests -v`

## Güvenlik ve sınırlar

Testler saf fonksiyonları ve yürütücünün AST'den **yalnız risk fonksiyonunu** sahte borsa yanıtlarıyla çalıştırır. Owner uygulaması, ağ bağlantısı, şifre, imzalı GET veya gerçek POST/DELETE testi yapılmaz. Tüm entegre testler ve canlıya alma ayrı inceleme gerektirir.

Bilinen sözleşme borcu: `order_filters.py` içindeki `margin_1x_usdt` çıktı adı eskidir; değer aslında kaldıraçla bölünmüş marjindir. Olası tüketicileri bozmamak için ilk pakette alan adı değiştirilmedi.

Kaynak dosyalar `SOURCE_SNAPSHOT.json` içindeki SHA-256 özetleri ile Mac'in aktif Owner SDK'sından yalnızca okunarak alınmıştır. Bu dalın GitHub'a gönderimi yapılmamıştır.
