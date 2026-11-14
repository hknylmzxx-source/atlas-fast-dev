# İ4 — 14 Kasım yerel ve doğrudan OneDrive doğrulaması

`Atlas_20261114_033005.tar.gz`: kaynak Mac arşivi ve Mac OneDrive eşitleme klasöründeki kopya 723.198.105 bayt. İkisinin SHA-256 değeri 03.30 raporuyla eşleşiyor: `eaf390496e12fe48bb2191b501811f7ff89d5313cfc03d54f48200544e54dcbb`. Rapor `PARTIAL`, 15 `PERMISSION_DENIED`, iki owner-private girdi. SHA eşleşmesi yerel kopyanın bütünlüğünü kanıtlar, eksik kapsamı düzeltmez.

Aynı OneDrive WebDAV yol yapısı, 6 Kasım kontrol dosyasını sunucuda çözdü, içerik alımı bağlantının boyut sınırında durdu (413). 14 Kasım yolu ise 403 ile “The system cannot find the file specified” döndürdü. Ad araması da boş. Bu sonuç sunucu kopyasını doğrulamaz. 403 nedeniyle silinmiş olduğu veya eşitleme hatasının nedeni ileri sürülmemelidir.

Üretim arşivlerine, görevlere ve FAST'e dokunulmadı. Sonraki teşhis OneDrive web hesabında depolama ve istemcinin dosya bazlı etkinlik/uyarı paneli, ardından gerekirse ayrı sentetik büyük dosya testi.
