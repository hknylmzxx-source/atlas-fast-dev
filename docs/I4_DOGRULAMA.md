# İ4 çevrimdışı deneme

Çalıştırma: `cd docs && python3 -B -m unittest -v i4_rotation_probe i4_archive_reader_probe`. Sonuç 10/10. Canlı dosya, servis veya zamanlayıcı değiştirilmedi.

Arşiv ve aktif dosya aynı kilit altında açılır, ardından okuma sabit dosya tanıtıcıları ve uzunluk sınırları üzerinden devam eder. Sonraki yazılar ve döndürme okuma anlık görüntüsünü değiştirmez. Testte sıralı akış, yarım satır reddi, gzip geri okuma özeti, uyuşmazlıkta orijinalin korunması ve kontrollü tar geri yüklemesi doğrulandı.

Bunlar örnek uygulama testleridir. Mevcut okuyucular bu API'ye geçirilmedi, gerçek snapshot tar komutu eşzamanlı döndürmeyle sınanmadı. Ham arşivin yerini gzip ile değiştirmek, mevcut yedek kapsamını değiştirmek veya üretim kurulumu bu dalın kapsamı dışındadır. Kurulumdan önce mevcut okuyucu ve yedek zinciri için regresyon, gerçek dosya sistemi davranışı ve izinler ile bağımsız inceleme gerekir.
