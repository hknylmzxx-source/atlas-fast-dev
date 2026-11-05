# ATLAS FAST — Paket 8: Tam `run_once` akışı (ağsız entegrasyon simülasyonu)

**Tarih:** 5 Kasım 2026  
**Konum:** `~/Atlas/ATLAS_FAST_DEV`  
**Dal:** `dev/20261105-preflight-diagnostics`  
**Başlangıç adayı:** `atlas-fast-v0.1.0-rc1-offline-20261105` (`6a532ff`; mevcut etiket değiştirilmedi)

## Çalıştırılan gerçek kod ile sahte bağımlılıklar

`tests/test_run_once_full_flow_offline.py`, `owner_single_execution.py` içindeki **tam `run_once` fonksiyonunu** kaynak AST'sinden derleyip izole bir Python namespace'inde çalıştırır. `_policy_inputs_market_last`, `_require_fresh_owner_approval`, `_check_approval_after_reservation` ve zaman ölçümü de aynı kaynak dosyasından seçilir.

Her `SignedVenue`, güvenlik kapısı, owner grant, emir yazma, STOP kanıtı, gelir geçmişi, policy fazı, risk rezervasyonu, gözetim, kapanış ve WAL çözümlenmesi fonksiyonu **tamamen sahte** olarak enjekte edilir. `sys.modules` geçici olarak sentetik modüllerle değiştirilir, test sonunda restore edilir. Her senaryo `TemporaryDirectory` kullanır. Gerçek Keychain, Binance SDK oturumu, HTTP, emir POST/DELETE, üretim WAL/ledger, telefon onayı veya canlı Owner işlemi çalıştırılmaz.

## Yeni 17 uçtan uca senaryo

- Normal simülasyonda: güvenlik kapıları → risk preflight → telefon grant → ikinci güncellik kontrolü → rezervasyon → ENTRY → STOP + doğrulama → TP1/TP2 → imzalıymış gibi simüle edilen FLAT → kapanış → WAL çözüm sırası.
- `production_entry` reddi, yeniden başlatma STOP kapısı reddi veya live-prerequisite reddi: Venue bile oluşturulmaz.
- Policy preflight reddi veya gelir sorgusu kesintisi: telefon onayı istenmez, giriş yok.
- Telefon onayı reddi, R2 koşulu eksikliği veya ikinci snapshot'ta açık pozisyon: rezervasyon ve ENTRY yok.
- İkinci piyasa sorgusu uzun sürer ve 15 saniye geçilirse: rezervasyon ve ENTRY yok.
- Rezervasyondan sonra onay süresi dolarsa: sıfır giriş kapanış yolu çağrılır, ENTRY yok.
- ENTRY gönderim sonucu belirsizse (sahte transport): tekrar ENTRY yok; STOP'a gidilmez; pozisyon var olduğu varsayıldığında manuel gözetim yolu çağrılır.
- STOP başarısızsa: TP gönderilmez; guard alarmı, gözetim ve temizleme yolu çağrılır.
- TP hatasında: ilerleyen TP yazılmaz ve incident/gözetim yolu çağrılır.
- Kesintisiz STOP koruması onaylanmazsa: FLAT olsa bile WAL çözümlenmez, owner-review sonucu döner.

## Sonuç

Komut:

```sh
cd "$HOME/Atlas/ATLAS_FAST_DEV"
"$HOME/Library/Application Support/AtlasFastOwnerSDK/.venv/bin/python" -B -m unittest discover -s tests -q
```

**132 / 132** çevrimdışı test başarılı (önceki 115 + yeni 17). `git diff --check` temiz.

## Sınırlamalar ve karar

**Bu senaryo tam `run_once` kontrol akışını sınar; üretim bağımlılıkları ile bir canlı E2E testi değildir.** Başarılı simülasyondaki sahte R2, telefon grant ve production-entry geçişleri gerçek güvenlik anahtarlarının açıldığı anlamına gelmez. `SignedVenue`, `one_write`, gerçek policy ledger ve STOP guard'ın donanım/ağ bütünlüğü bu yeni testte sahte olduğundan bunların bağımsız önceki kaynak-fonksiyon testleri canlı kanıtın yerini tutmaz.

Önceki RC1, `SenderLocked` nedeniyle gerçek read-only `query_order` yanıtını hâlâ doğrulayamadı. `continuous_stop_coverage_verified=false` ve `emergency_flatten_connected=false` açık engelleri devam ediyor.

**Karar:** NO-GO FOR LIVE. Bu geliştirme paketi RC1 etiketini değiştirmeden sonraki `dev/` commit'i olarak kaydedilir. İncelenip testleri genişletilmeden yeni aday yayımlanmaz.
