# G41 / Issue #6 — Saf offline deadline ve lease fence referans modeli

Tarih: 10 Kasım 2026. Görev tanımı kullanıcıdan alındı; GitHub/Issue okunmadı.
Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g41`.
Dal: `codex/g41-deadline-model`.
Başlangıç ve teslim HEAD: `131a1e23740ebc48f08fdd8b2a0c1e455f2c9704`.
G41 commit kimliği yok; kullanıcı talimatıyla commit/push/merge/PR yapılmadı.

**Hedefli G41: 45/45 başarılı. Tam paket: 536 test, 534 başarılı, 2 mevcut
SDK hatası, 0 assertion başarısızlığı, 0 atlama; çıkış 1. Tam paket yeşil değil.**
Claude'un bağımsız son incelemesi bekliyor; inceleme tamamlandı iddiası yoktur.

## Dosyalar ve izolasyon

Yalnız üç yeni dosya eklendi; değişiklikler commitsizdir:

- `deadline_fence_model_offline_g41.py`: standart kütüphane ile saf Python
  geçiş modeli; eski durum korunur, her çağrı yeni `Transition` döndürür.
- `tests/test_deadline_fence_model_offline_g41.py`: 45 bağımsız unittest;
  sentetik zaman ve açıkça sıralanmış yarış adımları.
- `G41_DEVIR.md`: sözleşme, test kayıtları, sınırlar ve bu devir.

Önce `AGENTS.md` ve `G40_DEVIR.md` okundu. Mevcut owner/STOP/guard/risk
dosyaları, `protective_wal_deadline_candidate.py`, `wal_writer_fence_candidate.py`,
G37–G40 kodları/testleri ve önceki devir notları değiştirilmedi.
Bu görevin devri bu yeni dosyadadır; `DEVIR_NOTU.md` korunmuştur.
Eski dosyalarda fark olmadığı `git diff --exit-code` ile doğrulandı.

Başka worktree/FAST/aktif owner, `/Users/Shared/Atlas`, `~/ATLAS_SAHIP`,
`~/Atlas/AKTIF_*`, Keychain veya credentials kullanılmadı. Ağ, gerçek Binance
API, internet, servis, paket kurulumu ve sistem değişikliği yoktur. Yeni model
yalnız `dataclasses`, `enum`, `math` import eder; yerel owner/SDK veya G37–G40
modülü import etmez. Modelde dosya, gerçek clock/sleep, thread/process, OS
kilidi, emir gönderme/iptal veya gerçek kilit açma fonksiyonu bulunmaz.
Tam paket mevcut offline fixture'ları çalıştırır; eksik SDK için aşağıdaki
iki hata oluşur. SDK kurulmadı veya yerine sahte paket/metadata konulmadı.
Kullanılmayan eski kod temizliği kapsam dışıdır; hiçbir modül silinmedi.

## Deadline ve lease sözleşmesi

1. `mock_issue_lease`: güvenilir mock otorite, scope/owner/epoch/generation ve
   expiry taşıyan yeni nesne üretir. Yeni nesil eskisini çitler; owner aynı
   kalsa da eski nesil geçersizdir. Aktif token ile **nesne kimliği** eşleşir.
   Alanları aynı kopya, başka modelden token, bool/readiness iddiası veya
   eksik token kanıt değildir. Bu yalnız mock yetki modelidir; gerçek imza,
   kimlik doğrulama veya güvenilir lease altyapısı değildir.
2. `begin`: timeout çağrının kendi sentetik `now_s` anından başlar. Geçmiş
   `LOCKED` bildirimi deadline başlangıcını veya kalan kilit ömrünü belirlemez.
   Pozitif bütçe için `now_s < deadline` gerekir; eşitlik sürenin dolmasıdır.
   Bütçe `0..0.5`, poll `0 < poll_s <= 0.05` saniyedir. NaN/inf/bool, geçersiz
   değerler ve float toplamasında temsil edilemeyen pozitif bütçe reddedilir.
3. `try_fence`: her denemeden **önce** deadline, lease nesnesi/epoch/nesil ve
   lease expiry yeniden doğrulanır. Meşgulse yalnız mutation öncesi bekleme
   önerilir: `min(poll_s, kalan_bütçe)`. Sleep/döngü kendi kendine çalışmaz.
   Scheduler oversleep sonrasında fence boş olsa da süre bittiyse yeni fence
   denemesi veya WAL mutation yapılmaz. Terminal istek canlandırılmaz.
4. `atomic_mutate`: fence sahibi olmalı; aynı soyut fence altında bütün
   deadline/lease kontrolleri tekrar yapılır. Aynı role/scope için duplicate
   kontrolü de fence altındadır. Son kontrol ve bir adet mock WAL ekleme
   **tek soyut atomik geçiş** olarak tanımlanır. Mutasyondan önce generation
   değişimi, lease expiry veya deadline aşımı WAL eklemeden reddedilir.
5. `timeout_s=0`: başlangıç model anında tek nonblocking acquisition denemesi;
   mutation da aynı model anında olmalıdır. Busy sonucu terminaldir, sleep ve
   ikinci deneme yoktur. İlk denemeden veya fence alındıktan sonra saat bir
   temsil edilebilir adım ilerlese bile mutation reddedilir. Bu, eski kodun
   sıfır bütçeyle koşulsuz append davranışına uygulanmış bir değişiklik değildir;
   yeni modelin açık, daha sıkı sözleşmesidir. Fsync geç biterse UNKNOWN olur.

## Fsync, crash/restart ve STOP

- Mutation sonrası `finish_fsync`, dosya ve dizin için ayrı sentetik sonuç
  ister. İkisi de `FsyncOutcome.OK` ve ACK zamanında değilse başarı verilmez.
  Hata/UNKNOWN/geç ACK, lease kaybı veya geçersiz saat `UNKNOWN/NO_RETRY`
  üretir; scope karantinaya alınır. Aynı isteğin tekrar mutation/acquisition
  çağrısı ikinci WAL oluşturmaz. Sonradan olumlu fsync bildirimi UNKNOWN'u
  temizlemez; yeni istek kimliği de karantinayı aşamaz.
- Her iki fsync için OK bildirilse ama ACK deadline'dan sonra gelse, mock
  dayanıklılık olgusu `durable=True` olarak korunur; istek yine UNKNOWN ve
  NO_RETRY'dır. "Timeout oldu, hiç yazılmadı" çıkarımı yapılmaz. Gerçek
  fsync'in zamanında biteceği garantisi veya fiziksel disk kanıtı yoktur.
- `crash_restart`, epoch'u artırır, aktif token'ı geçersiz kılar ve tüm yeni
  mutation yollarını replay-only kapatır. Mutation öncesindeki istekler
  reddedilir; mutation sonrası tamamlanmamış istekler UNKNOWN kalır. Bilinen
  mock durable kayıt da restart sonrasında yeni yazım yetkisi vermez.
  Temizlik, pending silme, otomatik retry veya lease yenileyerek açılma yoktur.
- `wal`, soyut gözlemcinin mutation geçmişidir; gerçek diskin crash sonrası
  replay'i değildir. Kaydın güç kesintisinde kalması/kaybolması modellenmez.
  Kaydın görünmemesi sıfır yazım/yeniden deneme kanıtı sayılamaz; restart
  koşulsuz kapalı olduğundan bundan yazma izni çıkaran yol bulunmaz.
- `MockStopSnapshot` frozen gözlemdir. STOP, TP1 ve TP2 mock WAL kayıtları
  STOP'un varlığını yaratmaz, değiştirmez veya kanıtlamaz. TP başarısı, hatası,
  karantina ve restart boyunca aynı STOP nesnesi korunur. Ayrı rollerin WAL
  duplicate alanları ayrıdır; TP kaydı STOP rolündeki mock WAL ile karıştırılmaz.
  STOP onarımı/emri/iptali veya owner kilidi açma yolu yoktur.

Her `Decision`, kurucudan değiştirilemeyen şu alanlarla döner:

```text
entry_policy = NO_NEW_ENTRY
unlock_policy = NO_UNLOCK
entry_authorized = false
unlock = false
stop_proven = false
```

`WAIT` sonucundaki `WAIT_PRE_MUTATION_ONLY`, yalnız pre-mutation fence
contention içindir; emir retry yetkisi değildir. Diğer her sonuçta retry
politikası `NO_RETRY` olur. `DURABLE` bile yalnız mock WAL olgusudur.
Modeldeki `fence_owner=None`, yalnız bellek alanının bookkeeping işlemidir;
gerçek güvenlik kilidi açılması veya owner yetkisi anlamına gelmez.

## G40 yarışları ve TOCTOU sınırı

G40 race 1 için test, `LOCKED` sonrasında 150 ms geçmiş ve fence bırakılmışken
çağrının kendi yeni bütçesiyle başlayabildiğini gösterir; bu durumda fixture'ın
koşulsuz timeout beklemesi doğru değildir. Readiness bool'u lease yerine
sunulursa reddedilir; eski generation güncel yetki sayılmaz.

G40 race 2 için 5 ms poll önerisinden sonra sentetik saat 150 ms ilerletilir.
Fence artık boş olsa da hâlâ meşgul olsa da ikinci acquisition/mutation yoktur.
Fence öncesi ve fence sonrası preemption ayrı testlidir. İki yazıcı sırayla
aynı role/scope için çalıştığında en fazla biri ekler; süresi dolmuş yazıcı,
yeni yazıcı fence aldıktan sonra geri dönüp kayıt oluşturamaz.

Bu sonuçlar **sıralı geçişler ve atomik model adımı varsayımı** altında geçer.
`now_s` güvenilen test harness'inden gelir; gerçek saatin veya scheduler'ın
doğrulanması değildir. Kaynakta kontrol ile tuple ekleme arasında callback
olmaması Python/OS preemption penceresini kapatmaz. Gerçek uygulamada tüm
yazarları kapsayan fence/lease otoritesi, güncel generation ve monotonic saat
kontrolünün storage mutation ile aynı linearization noktasında doğrulanması
ayrıca tasarlanmalıdır. Her yazıcı katılmıyorsa garanti yoktur; sert gerçek
zaman sınırı ve fsync tamamlanma süresi burada kanıtlanmamıştır.

Immutable eski state iki harness'e verilirse iki farklı geçerli tahmin dalı
oluşturulabilir; bunun fiziksel arbitration sağlamadığı ayrıca çalışır testle
gösterilir. Her sonraki adımın tek otoritenin son state'i üzerinden yürütülmesi
model sözleşmesidir. Frozen nesne/nesne kimliği düşmanca Python koduna karşı
sandbox veya güven kökü değildir. G40'ın mevcut iki yarışı eski kaynak ve
fixture'larda durmaktadır; canlıya uygulanmış düzeltme yoktur.

## Çalıştırma ve test kaydı

Worktree kökünde sırasıyla çalıştırıldı:

```sh
python3 -B -m unittest discover -s tests -p test_deadline_fence_model_offline_g41.py -v
python3 -B -m unittest discover -s tests -v
```

| Koşu | Test / başarılı | Assertion / hata / atlama | Süre | Çıkış |
| --- | --- | --- | --- | --- |
| İlk G41 geliştirme koşusu | 40 / 39 | 0 / 1 / 0 | 0.027 sn | 1 |
| Düzeltme ve ek sınır testleri sonrası hedefli G41 | **45 / 45** | **0 / 0 / 0** | 0.024 sn | **0** |
| Son tam paket | **536 / 534** | **0 / 2 / 0** | 12.118 sn | **1** |

İlk koşudaki hata yeni testteki exception beklentisidir:
`dataclasses.replace(decision, entry_policy="ALLOW")`, Python 3.13'te
`init=False` alan için `ValueError` yerine `TypeError` verir. Test, bu iki
geçerli exception türünü kabul edecek şekilde düzeltildi; alanın
değiştirilemeyeceği assertion'ı korundu. Aynı düzeltme adımında mutation
sonrası acquisition çağrısı da UNKNOWN/NO_RETRY olarak kapatıldı ve beş
ek deadline/race/saat sınır testi eklendi. Başarısız ilk koşu gizlenmedi.

Tam paketin mevcut iki hatası:

1. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
2. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError: No package metadata was found for binance-sdk-derivatives-trading-usds-futures`.

Bunlar G40'ta kaydedilen hatalarla aynıdır; testler değiştirilmedi,
atlatılmadı, skip edilmedi. Bu koşuda eski gerçek saat testi geçti; tek doğal
geçiş zamanlama yarışının giderildiğini kanıtlamaz. Sonucu yeşil göstermek
için tekrar koşusu veya bağımlılık kurulumu yapılmadı.
Yerel çıktılar `/private/tmp/g41-target-tests-20261110.log` ve
`/private/tmp/g41-full-tests-20261110.log` dosyalarındadır.

## Açık işler ve sonraki güvenli adım

- G41 offline model/test teslimi hazır; tam paket SDK bağımlılığı nedeniyle
  başarılı değildir. G37–G40 için düzeltme veya canlı entegrasyon yapılmadı.
- Sonraki güvenli adım Claude'un bu üç yeni dosyayı ve sözleşme varsayımlarını
  bağımsız incelemesi, hedefli ve tam testleri kendisinin çalıştırmasıdır.
  Claude bu oturumda çağrılmadı; çapraz inceleme bekliyor.
- Bu offline teslim için ek kullanıcı onayı gerekmiyor. Paket kurulumu,
  eski kritik dosya değişikliği, gerçek lease/storage uyarlaması ve canlı
  uygulama ayrı görev/onay/inceleme gerektirir; burada başlatılmadı.
- Geri dönüş: üç yeni dosyayı uygulamaya bağlamamak yeterlidir; mevcut akışa
  import/çağrı eklenmedi. Otomatik silme veya eski dosya geri yazımı yapılmadı.
- Son statik kontrol: iki yeni Python dosyasının AST/sözdizimi ve üç yeni
  dosyanın boşluk/satır sonu kontrolleri başarılıdır. Modelin importları yalnız
  yukarıdaki standart kütüphane modülleridir; mevcut dosyalarda Git farkı yoktur.
- Kullanıcının talimatı gereği commit/push/merge/PR yok; ajan teslimden sonra
  durur, başka worktree veya Claude üzerinde devam işlemi başlatmaz.
