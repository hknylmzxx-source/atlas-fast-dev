# İ4 — File Provider Syncing işareti, 14 Kasım 2026

Salt okunur Mac UID 501 incelemesinde `fileproviderctl evaluate <dosya>` çıktısının **Decorations** bölümünde önemli fark görüldü.

| Atlas arşivi | OneDrive sunucusunda | File Provider decorations |
|---|---|---|
| 2026-11-06 | Evet | boş (Syncing yok) |
| 2026-11-07 | Hayır | `com.microsoft.OneDrive.FileProviderBadges.Syncing: "Syncing"` |
| 2026-11-12 | Hayır | `Syncing` |
| 2026-11-13 | Hayır | `Syncing` |
| 2026-11-14 | Hayır | `Syncing` |

Beş dosyanın tamamında `ViewOnline: YES` eylemi var; bu eylemin izinli olması hedef öğenin server listesinde gerçekten bulunduğunu **kanıtlamıyor**. Yerelde eksik dosyaların tamamı `isUploaded=1`, `isUploading=0`, `isSyncPaused=0`, `isExcludedFromSync=0` gösteriyor. Syncing süsleme/badge bilgisi bu bayraklardan daha ayırt edici, ama tek başına neden/ilerleme yüzdesi veya nihai hata kodu sağlamıyor.

**Yorum:** 7/12/13/14 Kasım büyük arşivleri büyük olasılıkla tamamlanmamış/takılmış OneDrive eşitlemesinde. 190 baytlık sentetik arşiv OneDrive sunucusundan geri indirilip SHA-256 doğrulandığı için küçük dosya yolu sağlıklı. Mac depolamasında ~73,7 GB boş; OneDrive paket kotası kullanıcı beyanıyla 1 TB, hesapta kullanılan miktar doğrulanmadı.

**Sonraki adım:** Mac OneDrive menü çubuğu simgesinden etkinlik/uyarı listesini ve web depolama kullanımını kontrol et; 14 Kasım arşivindeki senkronizasyon ilerlemesini veya hata kodunu incele. Üretim dosyalarını taşımadan, yeniden yüklemeden veya OneDrive'ı sıfırlamadan önce olası büyük dosya eşitleme kuyruğu incelenecek. `İ4` canlı sisteme kurulmadı.
