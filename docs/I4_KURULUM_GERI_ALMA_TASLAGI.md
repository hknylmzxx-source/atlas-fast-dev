# İ4 kurulum ve geri alma taslağı

**Durum: çevrimdışı aday. Canlı kurulum yapılmadı.** Çalışma tabanı `45315b03`. 10 okuyucu/yazıcı regresyonu, 11 yayın testi, kesinti, v4 geri yükleme ve 226.278.610 bayt / 385.285 satır ölçek denemesi geçti. Bu sonuçlar bütünleştirilmiş Mac servis kurulumu değildir.

## Kurulum öncesi kalan iş

- UID 501 yazıcı ve UID 502 yedekleyici için ortak kilit inode'unu hazırlama, izin/sahiplik doğrulaması ve değişmez kimlik kontrolünü somut entegrasyonla test et. 30 saniyelik elle başlatılan yol ile launchd kontrol ajanı aynı yazıcıya bağlanmalı.
- Çevrimdışı adaptörü gerçek v4 ve bulut görev sözleşmesine bağla. Gerçek komutlar, konumlar, rapor/son dosya sırası, hata ve PARTIAL dönüş kodları, raporun arşivle SHA eşleşmesi ve bulutun yalnız yayımlanmış dosyayı alması uçtan uca doğrulanmalı.
- Test kopyasında ilk 226 MB dosyanın devrini, ayrı kullanıcıları, yarım kalmış arşiv bağlantısını, okuma/geri yükleme, disk/izin hatası ve mevcut Bekçi sağlık sonuçlarını doğrula. Arşiv saklama ve descriptor sınırı belirlenmeden sıkıştırma, otomatik silme veya saklama değişikliği yok.

## Önerilen geçiş

1. Hedef dosya yolları, sürümler, sahiplik/mod ve aktif tetikleyicileri kaydet. Mevcut sağlam kopya, geri dönüş dosyaları, disk alanı ve bilinen iyi geri yüklemeyi doğrula.
2. Onaylı bakım aralığında tüm yazıcı yollarının durduğunu ve gece yedeği/bulut işleminin çalışmadığını kanıtla. Aktif günlük ve arşivlerde sıra, kayıt sayısı, SHA-256 ve inode ilişkisini kaydet. Yarım kalmış geçiş varsa önce tutarlı hale getir.
3. Ortak kilidi hazırla. Önce arşiv okuyucusu ve yayın öncesi yedek doğrulaması, ardından yazıcı entegrasyonu ve döndürme eşiği. Eski ve yeni bileşenler karışıkken döndürme açılmamalı.
4. Kurulu dosya SHA'larını ve iki UID erişimini kontrol et. Ayrı fixture ile ilk döndürme ve geri yükleme, ardından ilk gerçek yedek ve bulut alımının SHA/rapor karşılaştırması. İzleyen gerçek tur doğrulanmadan kurulum tamamlandı sayılmaz. Canlı emir oluşturma, değiştirme veya iptal etme yok.

## Geri alma

1. Yeni döndürmeyi durdur ve tüm yazıcı/yedek süreçlerinin hareketsiz olduğunu doğrula. Aktif dosyayı ve tüm arşiv parçalarını koru. Eski günlüğü yeni kayıtların üzerine geri kopyalama.
2. Yarım kalmış hard-link varsa tek inode'u bir kez say. Arşivler + aktif dosyayı önce ayrı bir kurtarma yolunda sıraya koy, kayıt sayısı ve SHA doğrula. 256 MiB v4 dosya sınırını ve orijinal dosya izinlerini kontrol et.
3. Gerekirse yeni yazıcıyı döndürme kapalı halde kullan ve arşiv okuyucusunu koru. Eski yalnız-aktif okuyucuya ancak eksiksiz birleşik akış ayrı testten geçip eski yedek zinciriyle uyumluysa dön. Aksi halde arşiv uyumlu okuyucuyu açık bırak ve veri saklama kapsamını açıkça bildir.
4. Yalnız sürüm değiştirmek yedek kapsamını veya geçmiş erişimini kanıtlamaz. İki UID erişimi, son sağlıklı yedek, bir sonraki Bekçi turu ve bulut durumu yeniden kontrol edilir.
