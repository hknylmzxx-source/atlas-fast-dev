# İ4 OneDrive sentetik eşitleme doğrulaması — 14 Kasım 2026

**Kapsam:** Üretim ve canlı yedekleri değiştirmeden, ofis Mac'inin UID 501 hesabında küçük sentetik bir arşivin OneDrive'a ulaşması ve sunucudan bağımsız geri indirilmesi.

1. Mac üzerinde senkron ağacı dışındaki `~/Library/Caches` altında 190 baytlık geçerli tar.gz üretildi ve SHA-256 hesaplandı.
2. Dosya `ATLAS_CLOUD_BACKUP/03_TEST_LAB/I4_SYNC_PROBE_20261114/i4_synthetic_20261114.tar.gz` yoluna hard-link olarak yayımlandı. Yalnız test klasörü oluşturuldu; gerçek `SNAPSHOTS` dosyaları değişmedi.
3. İlk OneDrive sunucu listelemesinde klasör görünürken dosya henüz görünmüyordu. Sonraki listelemede sunucu öğesi bulundu: `AF25FC69FEE8D2F3!s36c27b2268214f48b6d57856a0f0c895`, boyut 190 bayt.
4. İlk web-link üzerinden ham dosya getirme isteği Microsoft `serviceReadOnly` / HTTP 403 hatası verdi. Exact Graph drive/item kimliğiyle yinelenen ham indirme başarılı oldu.
5. Sunucudan geri indirilen `tar.gz` dosyasının yerel çalışma alanındaki SHA-256 değeri Mac kaynak özeti ile **birebir aynı**: `da18f8a5057cc2b2e1ecc78b91d3ac0154c3865a52d55fc8af21ede412823a9a`. Tar içinde `probe.txt` görüldü.
6. İlave kontrol dosyası `i4_control_20261114.txt` 53 bayt olarak sadece test klasöründe oluşturuldu; bu dosyanın sunucu sonucu henüz değerlendirilmiyor.

**Sonuç:** Küçük sentetik dosyada Mac → OneDrive sunucusu → bağımsız ham indirme → SHA-256 zinciri başarılı. Dosyanın sunucuda görünmesi anlık değil; ilk kontrol yanlış negatif olabiliyor. Bu test büyük tam Atlas arşivlerinin (7, 12, 13 Kasım) bulutta bulunduğunu, 03.30/03.45 üretim otomasyonlarının güvenilir olduğunu veya File Provider'ın tüm boyutlarda aynı çalıştığını kanıtlamaz.

**Sonraki iş:** Gerçek `SNAPSHOTS` klasöründe 7/12/13/14 Kasım arşivlerinin sunucu ve Mac yerel envanterini salt okunur karşılaştır. Eksik büyük dosyaların eşitleme durumu, bulut kapasitesi ve olası hataları araştır; mevcut dosyaların üzerine yazma/silme yok. Gerekirse 03.45 adayının File Provider'a uygun son dosya yayımlama stratejisini ayrı testle değerlendir. İ4 hâlâ kurulu değil; canlı FAST ve Binance emirlerine dokunulmadı.
