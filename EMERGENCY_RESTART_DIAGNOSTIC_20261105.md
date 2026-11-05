# ATLAS FAST — Acil Alarm ve Yeniden Başlatma Gate Tanılaması

**Tarih:** 5 Kasım 2026. **Mod:** İzole geliştirme/test, **NO-GO**.

## Uygulanan yeni çevrimdışı tanı

`emergency_restart_diagnostic_candidate.py` yalnızca çağıranın seçtiği özel bir WAL test dizini üzerinde çalışır. `verify_restart_diagnostic`, önce mevcut `emergency_latch_<scope>.json` alarm kayıtlarını arar, çağıran tarafından sağlanan eskiden mevcut güvenlik kapısı callback'ini çalıştırır, ardından alarm dosyalarını yeniden kontrol eder.

Alarm dosyasının içeriği bozuk veya symlink olsa da adından dolayı otomatik ret oluşur. Dizinin izinleri güvenilmezse, legacy gate `False` dönerse veya istisna üretirse sonuç fail-closed olur. Dönüş nesnesi `entry_authorized=False` ve `atomic_release_verified=False` olarak tasarlanmıştır.

`tests/test_emergency_restart_diagnostic_candidate.py` dosyasında 9 yeni senaryo test edildi. Toplam **196/196** çevrimdışı test başarılı.

## Kritik sınırlar

- Bu bileşen aktif `owner_single_execution.py` veya üretimdeki `startup_reconcile` zincirine bağlı **değil**.
- İki okuma arasındaki ve son okumadan sonraki TOCTOU boşluğunu tümüyle kapatmaz. Entry izninin gerçekten atomik olması için tüm yazarlar/resolver ve entry lease birlikte bir kanıt mekanizmasına katılmalıdır.
- Alarm latch'in ayrı yazılabiliyor olması gerçek STOP/TP emirinin gönderildiğinin veya aktif koruma sağladığının kanıtı değildir.
- Herhangi bir signed Binance sorgusu, emir POST/DELETE, Owner SDK aktif güncellemesi ve güvenlik izni değiştirme işlemi yapılmadı.
- Sıfır-ENTRY WAL otomatik çözümü hâlâ kapalıdır.
- Gerçek signed order-absence, kesintisiz STOP doğrulaması ve acil kanal bağlantısı engelleri devam etmektedir.

**Karar: NO-GO FOR LIVE.**
