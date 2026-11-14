# İ4 — OneDrive motoru ek kontrolü (14 Kasım 2026)

## Salt okunur doğrulama
- Mac UID 501 `fileproviderctl evaluate` ile dört eksik tam Atlas arşivinin (7, 12, 13, 14 Kasım) hâlâ `Syncing` rozetinde olduğu, `isUploaded=1` ve `isUploading=0` gösterdiği tekrar doğrulandı.
- Bağlı kişisel OneDrive `ATLAS_CLOUD_BACKUP/SNAPSHOTS` sunucu envanterinde Kasım tam Atlas arşivleri yalnız 1–6 Kasım. Yeni bir tam arşiv sunucuya ulaşmış görünmüyor.
- `fileproviderctl dump -l` ile OneDrive File Provider alanı incelendi: `scheduling state: running`, `pending set watched`, `error generation: 691`, `pending-indexable-count: 0`. Buradaki `error generation` bir hata sayısı olarak yorumlanmamalıdır. `upload progress` nesnesi görünüyor ama belirli arşiv için ilerleme yüzdesi alınamadı.
- File Provider dökümünde farklı sağlayıcılara ve eski kayıtlara ait çok sayıda hata metni var. Bunların hedef Atlas arşivleriyle doğrudan bağlantısı teyit edilmedi; kök neden olarak kullanılamaz.
- UI denemesi macOS yardımcı erişim izni reddi nedeniyle sonuçsuz. Hiçbir uygulama, dosya veya izin değiştirilmedi.

## Sonuç
Yerel `isUploaded=1` değeri sunucu envanteri ile çelişiyor. `Syncing` rozetleri, eşitlemenin dört büyük arşiv için tamamlanmadığına işaret ediyor. OneDrive motoru çalışıyor; sorunun tam nedeni ve hesabın kullanılan kotası hâlâ bilinmiyor.

## Devam için somut test
1. OneDrive Mac menü çubuğu simgesinden **eşitleniyor** dosyaları, bekleyen MB/GB ve hata kodlarını kontrol et. Ekran görüntüsünü kanıt olarak sakla.
2. OneDrive web hesap ekranında kullanılan alanı 1 TB toplam kotayla karşılaştır. Paket kotası toplam alanı ifade eder, boş kota değil.
3. Arşivlerde hareket varsa sunucu envanterini tekrar sorgula. Sunucuda varlığı ve mümkünse SHA-256 doğrulaması olmadan bulut kurtarma noktası başarıya geçirilmemeli.
4. Canlı 03.45 görevinde `OK_COPIED` ile buluta gerçekten ulaşmayı ayrı durumlar olarak işaretleyecek entegrasyon yalnız test ortamında hazırlanmalı. Üretimde değişiklik ayrı onay.

**Güvenlik:** İ4 canlıya kurulmadı; arşiv taşıma/silme, OneDrive sıfırlama, Binance emirleri ve FAST canlı servisleri değişmedi.
