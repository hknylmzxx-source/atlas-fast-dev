# İ4 tam arşiv bulut envanteri — 14 Kasım 2026

Salt okunur Mac ve OneDrive sunucu envanteri karşılaştırıldı. Kaynak: ofis Mac UID 501 `~/Library/CloudStorage/OneDrive-*/ATLAS_CLOUD_BACKUP/SNAPSHOTS` ve bağlı OneDrive drive `AF25FC69FEE8D2F3` içindeki `ATLAS_CLOUD_BACKUP/SNAPSHOTS`.

**Sunucu ve Mac'te eşleşen tam arşivler:** 1–6 Kasım 2026; 6 Kasım boyutu 372.722.289 bayt.

**Mac'te var, sunucuda yok:** 
- `Atlas_20261107_033005.tar.gz` — 376.828.991 bayt
- `Atlas_20261112_101344.tar.gz` — 719.111.315 bayt
- `Atlas_20261113_033005.tar.gz` — 721.842.096 bayt
- `Atlas_20261114_033005.tar.gz` — 723.198.105 bayt

11/08–11/11 için aynı örüntüde yerel tam arşiv de listelenmedi; bunları bulutta eksik dosya olarak değil, envanter dışı günler olarak değerlendir. Diğer `Hafıza` veya rapor türleri bu karşılaştırmaya dahil edilmedi.

14 Kasım sentetik 190 baytlık arşiv için OneDrive server → ham indirme → SHA-256 başarıyla doğrulandı; ayrıntılar `I4_ONEDRIVE_SENTETIK_20261114.md`. Bu küçük dosya testi 376–723 MB tam arşivlerin eşitlendiğini kanıtlamaz.

**Kritik açık konu:** Yerel `OK_COPIED` tam bulut yedeği demek değildir; geçerli bulut tam Atlas kurtarma noktası hâlâ 6 Kasım görünüyor. Büyük dosyaların File Provider senkronizasyon durumu, olası kota, dosya türü/sahiplik/izleme ve gerçek hata kayıtları salt okunur incelenmeli. Test dışındaki dosyalar değiştirilmedi, gerçek İ4 kurulumu yapılmadı.
