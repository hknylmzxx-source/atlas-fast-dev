# İ4 OneDrive istemci ve sunucu durum farkı

14 Kasım 2026, salt okunur inceleme. OneDrive çalışıyor. Mac'te 73,7 GB yerel boş alan ölçüldü. Bu, OneDrive hesap kotası değildir. Kullanılan bağlantı hesap kotası döndürmedi.

7, 12, 13 ve 14 Kasım Atlas arşivleri Mac'te yerel bloklarıyla mevcut. File Provider dört dosya için de isUploaded=1, isUploading=0, isExcludedFromSync=0, isSyncPaused=0 ve hasUnresolvedConflicts=0 döndürdü. Buna rağmen aynı kişisel OneDrive'ın SNAPSHOTS sunucu listesinde ve dört ayrı ad aramasında hiçbiri görünmedi. SNAPSHOTS 77 öğe döndürdü. 6 Kasım Atlas ve 14 Kasım Hafıza arşivleri listede. Sentetik arşiv ve 53 baytlık kontrol dosyası ayrı test klasöründe sunucuda listeleniyor.

24 saatlik birleşik günlük sorgusu zaman aşımına uğradı. 14 Kasım 03.45 çevresindeki dar sorgu tamamlandı, ancak dönen satırlarda hedef dosya adına veya kota/depolama hatasına ilişkin açık kanıt bulunmadı. Kök neden belirlenmedi. İstemci bayrağı veya yerel OK_COPIED, sunucuya ulaşma kanıtı değildir.

Önerilen sonraki teşhis: OneDrive web depolama göstergesini ve Mac OneDrive etkinlik/hata panelini kontrol et. Mümkünse 14 Kasım yerel dosyasında Çevrimiçi Görüntüle ile açılan öğenin kimliğini karşılaştır. Üretim arşivlerini yeniden yükleme, üzerine yazma veya silme yapılmadı. İ4 üretime kurulmadı.
