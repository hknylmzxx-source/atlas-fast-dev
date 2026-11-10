# G42 / Issue #7 — Protective deadline standalone davranış adayı

Tarih: 10 Kasım 2026. Görev kullanıcıdan alındı; GitHub/Issue okunmadı.
Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g42`.
Dal: `codex/g42-protective-deadline`.
Başlangıç ve teslim HEAD: `8f9d390fbfe59ef28fd2bad22bd1e4ebf45e163e` (G41).
Yeni commit yok; bu üç dosya commitsizdir. Commit/push/merge/PR yapılmadı.

**Son hedefli koşu: 54/54 başarılı. Son tam paket: 590 test, 588 başarılı,
2 mevcut SDK hatası, 0 assertion başarısızlığı, 0 atlama; çıkış 1.
Tam paket yeşil değildir. Claude'un bağımsız son incelemesi bekliyor.**

## Dosyalar ve kapsam

Yalnız şu üç yeni dosya oluşturuldu:

- `protective_deadline_patch_candidate_g42.py`: gerçek döngü, nonblocking
  fence context manager ve append/fsync callback'lerini çalıştıran aday.
- `tests/test_protective_deadline_patch_candidate_g42.py`: 54 yeni unittest;
  sentetik saat, yan etkili mock storage ve iki parent/child temp-fence testi.
- `G42_DEVIR.md`: sözleşme, test sonuçları, sınırlar ve bu devir.

Önce `AGENTS.md`, `G40_DEVIR.md`, `G41_DEVIR.md` okundu. Ardından yalnız
metin olarak `protective_wal_deadline_candidate.py` ve
`wal_writer_fence_candidate.py` incelendi. İki dosya değiştirilmedi/import
edilmedi. Owner/STOP/guard/risk kaynakları, bütün mevcut testler, G37–G41
dosyaları ve `DEVIR_NOTU.md` değiştirilmedi. Mevcut izlenen dosyaların HEAD'e
göre farkı yoktur. Yeni adaya mevcut akıştan import/çağrı eklenmedi;
üretime uygulanmış düzeltme iddiası yoktur. G40'ın eski davranışı korunuyor.

Başka worktree, canlı dizinler, `/Users/Shared/Atlas`, `~/ATLAS_SAHIP`,
`~/Atlas/AKTIF_*`, Keychain, token/secret veya canlı yapılandırma okunmadı.
Ağ, gerçek Binance REST, owner SDK, servis restart, emir/STOP/kilit temizliği,
paket/venv kurulumu yoktur. Tam pakette mevcut iki SDK testi eksik import ve
metadata hatası verdi; SDK yüklenmedi veya sahte paket/metadata eklenmedi.
Yeni aday yalnız standart kütüphane import eder; kendi dosya/ağ adaptörü yoktur.

## Çalıştırılan davranış ve callback sözleşmesi

`ProtectiveDeadlinePatchCandidate.run(Intent, Lease, timeout_s, poll_s)`
callback'leri gerçekten çağırır; yalnız saf durum modeli veya test beklentisi
değildir. `Intent`, scope/role/identity/symbol/client_id taşır. Rollerin STOP,
TP1 ve TP2 olması yalnız WAL etiketidir; hiçbir borsa işlemi yapılmaz.

`Callbacks` şu bağımsız offline girdileri ister:

1. `acquire(lease)`: tek nonblocking acquisition yapan context manager;
   `FenceGrant` döndürür. Yalnız acquisition safhasındaki `FenceBusy`, append
   başlamadan tekrar denenebilir. Diğer hatalar ve body/exit içindeki
   `FenceBusy` terminaldir.
2. `lease_is_current(lease)`: güncel scope/owner/epoch/generation/nesne
   yetkisini dış mock otorite doğrular. Sonuç tam `True` olmalıdır. Testte
   aktif lease ile nesne kimliği karşılaştırılır; aynı alanlı kopya geçmez.
3. `lookup(intent)`: aynı fence altında salt okunur `(scope, role)` taraması.
4. `append_once(intent)`: aynı fence altında tekil ekleme. Bütün yazarların
   aynı fence'e katılması ve storage'ın tekilliği koruması callback şartıdır.
5. `fsync_file(intent)` ve `fsync_directory()`: ayrı, açık `FsyncAck.OK`
   bildirimleri gerekir. `None`, UNKNOWN, hata veya beklenmeyen cevap başarı
   değildir. Bunlar test callback bildirimidir, fiziksel disk kanıtı değildir.

Callback'ler güvenilir ve belirtilen safhalara uygun olmalıdır. Aday,
keyfi callback'in dosya/ağ/owner erişimini engelleyen sandbox, gerçek lease
otoritesi veya güncel generation'ı kalıcı saklayan servis değildir.

## Deadline ve sıfır timeout

- Pozitif timeout çağrının kendi ilk monotonic örneğinden başlar;
  `0 < timeout_s <= 0.5`, `0 < poll_s <= 0.05` saniye sınırları vardır.
  NaN/inf/bool/geçersiz girdiler reddedilir. Pozitif bütçenin float toplamında
  temsil edilememesi ve gözlenen saatin geriye gitmesi de kapalı sonuç verir.
- İlk fence denemesinden **önce**, her retry öncesinde, fence alındıktan sonra
  ve lookup sonrasında **append callback'inden hemen önce** deadline ve lease
  yeniden kontrol edilir. `now == deadline` dolmuş süredir. Fence grant'in
  scope/owner/epoch/generation alanları lease ile eşleşmelidir; bool nesil
  veya epoch kabul edilmez. Başlangıç ve final kontrol ayrı testlidir.
- Yalnız mutation öncesi contention için `min(poll_s, kalan_bütçe)` uyunur.
  Sleep overshoot sonrasında fence boşalsa bile yeni acquisition/append yoktur.
  Fence hâlâ meşgulse de aynı şekilde yeni deneme yapılmaz. Deadline sonucu
  terminaldir; sonradan kilidin bırakılması cached sonucu canlandırmaz.
- `timeout_s=0` **tek nonblocking acquisition ve hiç sleep/retry olmaması**
  anlamındadır. Bu modda elapsed-time deadline uygulanmaz; lease expiry,
  güncel nesil ve idempotence kontrolleri yine uygulanır. Serbest fence altında
  başarılı append/fsync zaman alabilir. Bu, G41'in "aynı sentetik anda mutation"
  sözleşmesinden bilinçli farklı bir aday sözleşmesidir; eski kaynakta hiçbir
  semantik değişikliği yapılmadı. Sıfır timeout, fonksiyonun sıfır sürede veya
  tüm callback'lerin nonblocking bitmesi garantisi değildir.
- Pozitif modda append sonrası sync'ler, fence exit ve final ACK kontrolü de
  bütçe/lease doğrulamasından geçer. Geç kalan başarılı callback ACK'si bile
  `UNKNOWN/NO_RETRY` olur. Callback bloklanması/preemption kesilmez; bu kod
  sert gerçek zaman veya fonksiyonun belirtilen sürede dönüş garantisi vermez.
  Clock'un ilerlemesi ve callback sözleşmesi dış önkoşullardır.

## Append idempotence, UNKNOWN ve restart

- Terminal sonuç aynı instance'ta `(scope, role)` anahtarında saklanır.
  Aynı intent tekrar gelirse eski sonuç yeni callback çağrısı olmadan döner;
  bu tarihsel sonuç, yeni deadline/lease onayı değildir. Aynı anahtarda
  değişen identity/client_id vb. çakışma olarak reddedilir; eski kayıt yazılmaz.
- Başka instance aynı storage'da eşit kayıt görürse geçmiş fsync'ini bilemez:
  `PREEXISTING_RECORD_DURABILITY_UNKNOWN`, NO_RETRY ve scope karantinası
  döner. Çakışan kayıt üzerine yazılmaz. Fiziksel tekillik yalnız sınıfın
  belleğinden değil, paylaşılan fence ve append_once sözleşmesinden gelir.
- Append çağrılmadan hemen önce `append_attempted=True` olur. Bundan sonraki
  yakalanan exception, eksik fsync ACK, geç ACK, saat/lease/nesil kaybı veya
  fence exit hatası `UNKNOWN/NO_RETRY` üretir. Callback kısmen veya tamamen
  yazmış olabilir; silme, rollback, otomatik append retry veya unlock yoktur.
  `durability_confirmed=False`, "diskte yok" ya da "fsync başarısız oldu"
  kanıtı değildir. Her iki OK geç gelse de görünür kayıt korunur, durum UNKNOWN'dur.
- UNKNOWN scope'un tamamını aynı instance'ta karantinaya alır. Başka identity
  veya TP rolü bunu açamaz. İlgisiz scope kendi lease'i ile ayrı değerlendirilir.
  In-flight/reentrant ikinci çağrı beklemeden kapatılır; bu bellek mutex'idir.
- `mark_restart()` tek yönlü replay-only kapısını kapatır; kayıt/STOP silmez,
  lease yenilemez veya kapıyı yeniden açmaz. Lookup sırasında restart append'i
  engeller; append başladıktan sonra restart UNKNOWN üretir. Yeniden kurulan
  harness `replay_only=True` başlamalıdır; görünür kayıt olmaması izin değildir.
  Bellekteki cache/karantina gerçek crash'te kaybolur. Kalıcı replay/karantina
  altyapısı uygulanmadı; yeni instance'ı yanlışlıkla False ile başlatmak bu
  sınıfın engellediği bir durum değildir. Süreç ölümü/KeyboardInterrupt gibi
  olağan Exception dışında kesintiler için de dış replay-only otoritesi gerekir.

## STOP bağımsızlığı ve değişmeyen yetki alanları

Tüm `Decision` sonuçları, olumlu `ACKED` dahil, kurucuda değiştirilemeyen
şu alanları taşır:

```text
entry_authorized = false
unlock = false
stop_proven = false
retry_policy = NO_RETRY
```

Bu frozen Python nesneleri düşmanca Python koduna karşı güven kökü değildir.
Testte frozen `StopObservation` aynı nesne olarak kalır. TP1/TP2 ve STOP
etiketli WAL kayıtları STOP'un varlığını yaratmaz veya kanıtlamaz; başarı,
UNKNOWN ve restart STOP gözleminden bağımsızdır. Adayda STOP onarım/iptal,
owner kilidi açma veya entry çağrısı yoktur. Geçici callback advisory fence'in
ve bellek mutex'inin bırakılması owner/STOP güvenlik kilidinin açılması değildir.

## TOCTOU ve fsync için kanıtın sınırı

G40'ın sleep sonrası açık yeniden deneme penceresi yeni adayda testlerle
kapatıldı; **check -> gerçek mutation penceresi atomik hale getirilmedi**.
Saat veya lease/generation son kontrolden sonra, append callback'i içinde
değişebilir. İki test bu sınırı bilerek çalıştırır:

- Başlangıç 100.0, timeout 0.25 iken append callback'i saati 101.0'a taşır
  ve gerçekten bir mock kayıt ekler. Aday UNKNOWN döndürür; geç yazımı geri
  alamaz. İkinci çağrı yeni append yapmaz.
- Aynı son kontrol sonrasında generation değişirse mock kayıt yine oluşabilir;
  son kontrol UNKNOWN üretir ve kayıt yerinde kalır.

Dolayısıyla "deadline geçtiyse hiç yazılamaz", "fsync OK fiziksel durability
kanıtıdır", "timeout yazılmadı demektir" veya "stale lease mutation atomik
engellendi" güvencesi verilmez. Üretim uyarlaması için gerçek storage mutation
ile aynı linearization noktasında güncel lease/fence/deadline doğrulaması ve
kalıcı crash/replay politikası ayrıca tasarlanmalıdır. Başarılı son kontrolden
sonraki preemption da ACK'nin dönüş zamanını garanti etmez.

İki süreç testi yalnız bu görevin başlattığı çocuk ile parent'ın temp-dir'deki
gerçek POSIX advisory fence'ini kullanır. Çocuk `LOCKED` sonrası zaman bazlı
bırakmaz; `RELEASE` bekler. Readiness/bırakma ACK bekleyişleri 5 saniye,
çocuk watchdog'u 10 saniye ile sınırlıdır; hata skip'e dönüştürülmez.
Temizlik yalnız fixture çocuğu ve `TemporaryDirectory` kapsamındadır.
Bir test bırakma ACK'sinden sonra sentetik saati deadline ötesine taşır;
ikinci acquisition/append olmadığını doğrular. Diğeri eski readiness sonrası
kilit zaten bırakılmışken yeni çağrının kendi bütçesiyle başlayabildiğini gösterir.
WAL yan etkileri mock bellektedir, fsync callback'leri sentetiktir; bu iki
test gerçek diskin crash dayanıklılığını veya ölçülmüş scheduler süresini kanıtlamaz.

## Test kaydı

Worktree kökünde, sıralı olarak hedefli ve ardından tam komut çalıştırıldı:

```sh
python3 -B -m unittest discover -s tests -p test_protective_deadline_patch_candidate_g42.py -v
python3 -B -m unittest discover -s tests -v
```

| Koşu | Test / başarılı | Assertion / hata / atlama | Süre | Çıkış |
| --- | --- | --- | --- | --- |
| İlk hedefli G42 | 53 / 53 | 0 / 0 / 0 | 0.203 sn | 0 |
| İlk tam paket | 589 / 587 | 0 / 2 / 0 | 9.181 sn | 1 |
| Son hedefli G42 | **54 / 54** | **0 / 0 / 0** | 0.295 sn | **0** |
| Son tam paket | **590 / 588** | **0 / 2 / 0** | 13.805 sn | **1** |

İlk koşu sonrasında lease scope tür doğrulaması sıkılaştırıldı, fence grant
kontrolünün okunabilirliği düzeltildi, fsync alt senaryolarında kullanılan
backend'in kendi STOP nesnesi ayrıca doğrulandı ve fence exit sırasında nesil
değişimi için bir test eklendi. Bu somut değişiklikler sonrası hedefli/tam
koşular tekrarlandı; yalnız yeşil sonuç seçmek için tekrar yapılmadı.
Yeni G42 testlerinde başarısız koşu oluşmadı. Eski testler değiştirilmedi,
gizlenmedi veya skip edilmedi. Eski gerçek saat testinin bu iki koşuda geçmesi
G40'taki zamanlama yarışının giderildiğini göstermez.

Her iki tam koşuda mevcut aynı iki hata:

1. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
2. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError: No package metadata was found for binance-sdk-derivatives-trading-usds-futures`.

Yerel loglar:
`/private/tmp/g42-target-tests-20261110.log`,
`/private/tmp/g42-full-tests-20261110.log`,
`/private/tmp/g42-target-tests-final-20261110.log`,
`/private/tmp/g42-full-tests-final-20261110.log`.

## Devir ve sonraki güvenli adım

- G42 aday/test teslimi hazır; tam paket eksik SDK nedeniyle başarılı değildir.
  Son statik kontrolde yeni Python/çocuk kodunun sözdizimi, üç dosyanın
  boşluk/satır sonları, tam dosya kapsamı ve mevcut dosyaların değişmediği
  doğrulandı. Hiçbir eski modül silinmedi; kod temizliği kapsam dışıdır.
- Sonraki güvenli adım Claude'un bu üç yeni dosyayı bağımsız incelemesi ve
  aynı hedefli/tam komutları kendisinin çalıştırmasıdır. Claude veya başka
  alt ajan bu oturumda çağrılmadı; çapraz inceleme tamamlandı iddiası yoktur.
- Bu offline teslim için ek kullanıcı onayı gerekmiyor. Eski kod/üretim
  entegrasyonu, kalıcı storage/lease altyapısı ve bağımlılık kurulumu ayrı
  görev/onay/inceleme konusudur; burada başlatılmadı.
- Geri dönüş: adayın mevcut akışa bağlı olmaması korunur; bu üç yeni dosya
  uygulamaya alınmaz. Eski kodu geri yazma, dosya silme veya canlı cleanup gerekmez.
- Dal/kimlik yukarıdadır; kullanıcının talimatı gereği commit/push/merge/PR yoktur.
