# İ4 Mac sahiplik geçişi denemesi

Ofis Mac'inde yalnız geçici /Users/Shared test dizini kullanıldı. Başlangıçta UID 502, GID 0, 0666 günlük ve ortak kilit oluşturdu. UID 501 eşiği aştı: eski inode arşivde UID 502, GID 0 ve orijinal mtime ile kaldı, yeni aktif dosya UID 501, GID 0, 0666 oldu. UID 502 yeni dosyaya eşik altında ekleme yaptı ve eşik üstünde döndürmesi reddedildi. Arşivde kayıt 1, aktif dosyada kayıtlar 2 ve 3 vardı. Geçici dizin kaldırıldı.

Çevrimdışı kesinti denemesinde, arşiv bağlantısı oluşturulduktan sonra aktif dosya değişimi hata verdi. Aktif yol kaybolmadı. Sonraki yetkili yazma, tek arşivle devam etti.

Üretimden önce: kesinti aralığında aktif dosya ve arşiv aynı inode'u gösterdiğinde yedekleme ve ölçüm okuyucuları çift kayıt kabul etmemeli. Bu durumun bütünleşik regresyonu ve yetkili yazıcı devri incelemesi henüz açık. Canlı izin, sahiplik, servis ve günlük değişmedi.
