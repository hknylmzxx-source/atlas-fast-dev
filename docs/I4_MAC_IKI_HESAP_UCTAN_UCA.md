# İ4 iki hesaplı Mac testi

Aday kodu `544af254`, kurulu v4 çekirdeğinin değiştirilmemiş kopyası ve tamamen sentetik veri. UID 502 gece göreviyle aynı Python 3.13 yolundan yedek oluşturdu. Raporun son arşiv adından önce hazır olduğu görüldü. Her iki çıktı 0600 + UID 501 okuma ACL'siyle yayımlandı. Sistem /usr/bin/python3 sürümü hashlib.file_digest sağlamadığından testte gerçek görevde sabitlenen Python 3.13 kullanıldı.

UID 501 yerel hedefe doğru arşivi kopyaladı ve kopyanın SHA-256 özeti raporla eşleşti. Eksik raporlu ve yanlış SHA-256 içeren iki arşiv için hedef dosya oluşmadı. Geçici dizin kaldırıldı. OneDrive, üretim dosyaları ve görevler değiştirilmedi.

## Gerçek görevle farklar

- 03.45 bulut sarmalayıcısı rapor olarak `raporlar/Atlas_YYYYMMDD_HHMMSS.report.json` kullanıyor. Mevcut dosyalar UID 502:GID 20 ve 0640, UID 501 bunları okuyabiliyor. Erişim politikası değişmeden önce diğer GID 20 tüketicileri ve gerçek sarmalayıcı yetkileri doğrulanmalı.
- Mevcut sarmalayıcı önce kopyalıyor, sonra rapor durumuna bakıyor. Doğrulama hem ilk kopya hem zaten mevcut kopya kontrolünden önce bağlanmalı. 26 saat tazelik ve PARTIAL_SNAPSHOT ayrımı korunmalı. Hata durumunda atomik ve taze durum kaydı gerekli.
- Yerel APFS testi OneDrive File Provider davranışını kanıtlamaz. Doğrulanmamış geçici kopya senkron klasöründe oluşturulmamalı. Aynı dosya sisteminde, senkron ağacının dışından yayımlama ve geri okuma davranışı ayrıca doğrulanmalı.
- Bu sonuç yerel bileşenlerin iki hesapla birlikte çalıştığını kanıtlar. 03.30 ve 03.45 sarmalayıcılarının kurulumu ya da gerçek bulut aktarımı değildir.
