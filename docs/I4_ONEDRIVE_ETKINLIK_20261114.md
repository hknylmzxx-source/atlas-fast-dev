# İ4 — 14 Kasım OneDrive etkinlik incelemesi

Salt okunur takip:

- macOS `System Events`, OneDrive ve OneDrive File Provider işlemlerini gördü; ancak UI pencerelerini okuma isteği `osascript için yardımcı erişime izin verilmiyor (-1728)` nedeniyle reddedildi. Kullanıcı izni değiştirilmedi.
- `fileproviderctl evaluate`, 6 Kasım sunucuda görünen arşivde Syncing işareti vermiyor; 7/12/13/14 Kasım sunucuda görünmeyen tam arşivlerin her birinde `com.microsoft.OneDrive.FileProviderBadges.Syncing: "Syncing"` gösteriyor. Dördünde `ViewOnline: YES` eylemi var; bu, dosyanın sunucuda gerçekten bulunduğunu doğrulamaz.
- OneDrive sunucu klasörünün bir kez daha salt okunur listelenmesinde 77 öğe; kasım ayı tam Atlas arşivleri yalnız 1–6 Kasım. Kayıp dört arşiv hâlâ listelenmedi.
- `fileproviderctl dump -l` komutu salt okunur tamamlandı. Tüm File Provider alanlarını içerdiğinden, ham karma günlükte geçen eski `Excluded From Sync Due To Filename`, `NSURLError -1001`, `OneDrive Hydration` gibi mesajları **hedef Atlas dosyalarının nedeni** olarak yorumlamak doğru değil. Hedef öğeyle doğrudan ilişkilendirilmiş bir hata kodu alınmadı.
- Kullanıcı OneDrive paket kotasının 1 TB olduğunu bildirdi; kullanılan/bakiye miktarı bilinmiyor.

**Sonuç:** 7/12/13/14 Kasım tam arşivlerinde takılmış/bekleyen eşitleme belirtisi güçlendi; kök neden teyit edilmedi. Üretim dosyası taşınmadı, silinmedi veya yeniden yüklenmedi; OneDrive hesabı sıfırlanmadı ve İ4 kurulu değil.

**Bir sonraki somut kanıt:** Mac menü çubuğu → OneDrive bulut simgesi → etkinlik/ayrıntılar; arşiv adları için görünen ilerleme veya hata kodu, ayrıca OneDrive depolama kullanılan alanı. Mümkünse 14 Kasım arşivi Finder'da sağ tıklama `Çevrimiçi Görüntüle` bağlantısı (doğrulanabilir öğe kimliği). Onay olmadan macOS erişim izni veya canlı görev değiştirilmemeli.
