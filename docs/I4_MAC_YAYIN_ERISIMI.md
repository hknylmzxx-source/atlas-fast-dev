# İ4 Mac yayın erişimi

Geçici /Users/Shared dizininde, sentetik veri ile ve gerçek iki Mac hesabıyla doğrulandı. UID 502:GID 0 tarafından yazılan 0640 raporu UID 501 okuyamadı. 0644 rapor okunuyor, fakat erişimi herkese açmak gerekli değil.

UID 502'nin ürettiği rapor ve aday arşiv için 0600 temel izin ve yalnız UID 501'e okuma veren macOS ACL kullanıldı. Son Atlas_*.tar.gz adı henüz yokken UID 501 iki dosyayı okudu ve rapordaki SHA-256 değerini aday baytlarıyla eşleştirdi. Geçici dosyalar kaldırıldı. Canlı dosya/ACL/servis değişmedi.

Her iki UID aynı test kilidini aynı aygıt ve inode üzerinden açtı (16777233 / 98462127). Bu, önceki çekişme testiyle birlikte iki hesabın bir kilidi paylaşabildiğini kanıtlar. Kurulumda kilit yolu bu testteki gibi UID 502'nin yönettiği ve UID 501'in dizin girdisini değiştiremediği bir üst dizinde tutulmalı; iki taraf aynı inode'u kontrol etmeli. Canlı 0777 günlük dizinindeki kilit adını değiştirme riski bu testle giderilmiş sayılmaz.

Sonraki entegrasyon: doğrulama raporu ve aday arşive ACL'yi son isim görünmeden uygula. Gerçek rapor/dosya yolları, her iki hizmet hesabının erişimi ve bulut okuyucusunun arşiv+rapor+SHA sözleşmesini test kopyasında doğrula. İzin/ACL uygulaması başarısızsa arşivi yayımlama. Henüz kod veya gece işi bu ACL'yi uygulamıyor.
