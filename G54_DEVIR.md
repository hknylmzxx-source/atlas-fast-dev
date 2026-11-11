# G54 — Owner geliştirme kopyasında ortak WAL writer fence entegrasyonu

Tarih: 11.11.2026. **Yalnız geliştirme yaması; canlıya yüklenmedi.**
G54 hedefli testler başarılıdır; tam paket aşağıdaki beş başarısız kaynak
beklentisi nedeniyle yeşil değildir. Claude bağımsız incelemesi bekliyor.
Bu teslim canlıya geçiş, emir, STOP/kilit değişikliği veya merge onayı değildir.

## Çalışma alanı ve yetki

- Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g54`.
- Dal: `codex/g54-wal-owner-integration`.
- Baz ve değişmeyen HEAD: `3c223d5cf3107df02202b081c38b7fb743267a13`.
- Değişiklik commit'i **yoktur**; yama çalışma ağacında, stage edilmemiştir.
  Kullanıcının açık talimatıyla commit/push/merge/PR yapılmadı.
- Önce `AGENTS.md`, `G52_DEVIR.md`, `G45_ENTEGRASYON_SOZLESMESI.md`, iki
  WAL modülü, owner'ın ilgili yazım/lease/emergency/CANCEL/rezervasyon/restart
  yolları ve ilgili testler incelendi.
- Kullanıcı kritik owner/WAL geliştirme yamasını açıkça onayladı.
  `DEVIR_NOTU.md` ve başka kaynaklar değiştirilmedi; göreve özel devir burada.
- G41 worktree'si incelenmedi/değiştirilmedi; yalnız kullanıcının açıkça izin
  verdiği `.venv/bin/python` salt okunur yürütüldü. Paket kurulmadı.

## Dosya envanteri

| Dosya | Değişiklik |
| --- | --- |
| `owner_single_execution.py` | Üç WAL yazıcısında gerçek adapter çağrıları; incident persist hatasının mevcut gözetimi atlamasını önleyen `run_once` hata yolu. |
| `wal_owner_write_adapter_candidate.py` | CANCEL rolleri, legacy şema doğrulaması, caller detail kopyası, ilk incident kanıtını doğrulayıp yeniden fsync etme seçeneği. |
| `wal_writer_fence_candidate.py` | Ortak nonblocking flock, bounded/strict WAL okuması, UID/hardlink/özel dosya kontrolleri, dizin inode doğrulaması, fail-closed durability/cleanup hataları. |
| `tests/test_g54_owner_wal_integration.py` | 62 yeni test: gerçek owner AST parçaları, sentetik venue ve geçici dizin; bazdan alınan koruma AST özetleri. |
| `tests/test_g54_wal_processes.py` | 25 yeni test: ayrı OS süreçleri, gerçek flock, pipe bariyerleri, geç yazıcılar, sentetik mutation/intervention kilitleri, child kesintileri. |
| `G54_DEVIR.md` | Bu teslim ve Claude inceleme alanı. |

## Gerçek çağrı entegrasyonu ve kilit sırası

Owner'ın sabit private `LOG` hedefi ve **expected_uid=501** korunur. Yeni
bir alternatif owner, CLI prototipi veya order gönderim mekanizması yoktur.
`journal`, `emergency_note` ve `write_policy_reserve_record` artık kendi
WAL dosyalarını doğrudan açmaz; mevcut adapter üzerinden aynı
`.atlas_wal_writer_fence.lock` inode'u üzerinde `fcntl.flock(LOCK_EX|LOCK_NB)`
kritik bölümüne girer.

| Yazıcı ailesi | Gerçek owner yolu | Edinim / bırakma sırası |
| --- | --- | --- |
| ENTRY, STOP, TP1, TP2 | `one_write → journal → append_order_journal → append_record` | Mutation lease → kısa WAL fence → scan/append/file fsync/dir fsync → WAL fence bırak → mevcut `post_once` → mutation lease bırak → signed readback. |
| EMERGENCY | `emergency_flatten → _emergency_flatten_locked → journal` | Emergency intervention lock → WAL fence/append/bırak → mevcut `post_once` kendi mutation lease'i → intervention bırak. Gerçek `SignedVenue` emergency kapısı değişmedi. |
| CANCEL_STOP, CANCEL_TP1, CANCEL_TP2 | `cleanup_owned_exits → journal` | Mevcut signed FLAT/owned-exit kontrolü → WAL fence/append/bırak → mevcut `delete_once` kendi mutation lease'i. WAL tutulurken DELETE lease'i edinilmez. |
| POLICY_RESERVE | `risk_ledger_entry_gate → write_policy_reserve_record → append_reservation` | WAL fence/append/bırak → mevcut policy reservation. Ledger ile WAL tek atomik transaction yapılmış sayılmaz. |
| incident | `emergency_note → append_incident` | Ayrı mutation/intervention lease'i edinmeden aynı WAL fence; dosya **ve dizin** fsync. |

Fence altında signed GET/POST/DELETE, intervention lock edinimi veya mutation
lease edinimi yapılmaz. `one_write` mevcut mutation lease'ini WAL'dan önce
almaya devam eder. CANCEL/emergency append'leri bu lease'i WAL içinde
edinmez. Böylece WAL→mutation ve mutation→WAL şeklinde karşılıklı bekleme
oluşturan yeni bir nested yol eklenmez. Flock denemesi beklemesizdir.

`WAL_FENCE_BUSY`, owner `Blocked` hatasına, `one_write` sınırında mevcut
`PreSendRefused` hatasına dönüşür; **transient LeaseRefused yapılmaz**.
`place_stop_write` yalnız önceki mutation-lease busy durumunu tekrarlar.
WAL busy için retry/sleep/POST yoktur. Signed readback tekrarları mevcut
davranıştır; bunlar yeni POST değildir.

Incident fence/disk/şema hatası sessiz başarıya çevrilmez. `run_once` bu
etiketli `Blocked` durumunu yakalayıp mevcut `flatten_or_supervise` yolunu
çağırır, sonra hatayı yükseltir. Bu mevcut yol, gerçek SignedVenue'da
emergency gate reddinden sonra alarm ve signed-FLAT gözetimine gider.
Böylece incident append hatası açık pozisyonun gözetimini atlamaz. Yeni
order wire'ı, transport çağrısı, retry yetkisi veya koruma iddiası eklenmedi.

## Dosya, şema ve belirsizlik davranışı

- Private dizin, owner UID, mode ve son bileşen symlink kontrolleri korunur;
  dizin inode'u edinimden sonra, append öncesinde ve bırakmadan önce
  karşılaştırılır. Lock dosyası normal dosya, tek hardlink, aynı UID ve
  private izinlerle doğrulanır.
- Kayıtlar `dir_fd` ve `O_NOFOLLOW` ile okunur. Normal dosya/UID/private
  mode/tek hardlink şartları, **1..16384 byte** sınırı, UTF-8, duplicate JSON
  key ve NaN/Infinity/`1e999` reddi vardır. `O_NONBLOCK` FIFO üzerinde okuyucu
  bekleyip signed koruma yolunu asılı bırakmayı önler.
- Owner adapter'ı legacy role/intent/state/detail/filename/scope bağlarını
  fence altında doğrular. CANCEL_STOP'un mevcut `symbol` içermeyen detail
  biçimi korunur. Geçersiz veya bilinmeyen legacy kayıt sessizce atlanmaz.
- `O_EXCL|O_NOFOLLOW`, private dosya yaratma, file fsync → directory fsync
  sırası korunur. Kısmi dosya veya fsync hatasında dosya silinmez,
  overwrite/retry yapılmaz; `WAL_APPEND_UNKNOWN_NO_RETRY` ile reddedilir.
- Fence release hatası da başarı değildir; açık fd'ler kapanır, owner SDK
  dispatch'ine geçmez. Kimlik yeniden denendiğinde mevcut WAL tekrar
  gönderimi engeller.
- Normal duplicate order ve rezervasyon eski owner ret anlamını korur.
  Duplicate incident yalnız geçerli ilk incident doğrulanıp file/dir fsync
  tekrar tamamlandıktan sonra kabul edilir; ilk byte'lar korunur. Bozuk
  incident artık eski `FileExistsError:return` yoluyla sessizce kabul edilmez.
- Hedef scope için herhangi bir resolved dosya girdisinin varlığı yazımı
  reddetmeye yeterlidir; bunun içeriğinden çözüm yetkisi türetilmez.

`mark_scope_resolved` ve zero-entry auto-resolution **sürekli kapalıdır**;
ikisi de bazla aynı AST'ye sahiptir. Yeni owner entegrasyonu resolved marker
yazmaz ve resolver çağırmaz. Önceden var olan sentetik resolver yardımcıları
etkinleştirilmedi/değiştirilmedi. Mevcut tam suite'in bu yardımcıları sınayan
eski testleri yalnız kendi geçici dizinlerinde marker fixture'ları üretir;
owner veya gerçek WAL'a marker yazımı yoktur.

## Diff güvenlik incelemesi

Baz ile AST karşılaştırmasında değişen owner fonksiyonları **yalnız**
`journal`, `emergency_note`, `write_policy_reserve_record`, `run_once`.
Fonksiyon/sınıf ekleme veya kaldırma yoktur; üst seviye sabit atamaların
tamamı aynıdır. Değişen `run_once` kısmı yalnız incident hatası sonrası
mevcut gözetim yoluna yönlendiren dış exception handler'dır.

Bazdan alınan AST SHA256 testleri ayrıca şunların aynı kaldığını doğrular:
`SignedVenue` sınıfının tamamı (signed GET/POST/DELETE, token ve lease
sınırları), `make_guarded_request`, `owner_only`, global/intervention lock,
`one_write`, `place_stop_write`, emergency fonksiyonları, CANCEL cleanup,
`startup_reconcile` ve iki kapalı resolve yolu. Risk, margin, leverage,
STOP/R2, F01 slot ve G46/G48 kapıları değiştirilmedi.

Bu, geliştiren Codex'in kendi diff kontrolüdür; bağımsız Claude onayı değildir.
`git diff --check` başarılıdır. Mevcut test veya unrelated kaynak dosyası
değiştirilmedi; test skip/disable uygulanmadı. Modül silinmedi; temizlik veya
canlı geri dönüş işlemi yapılmadı. Yama commit edilmemiş küçük source delta
olarak geri alınabilir; reset/rebase/merge uygulanmadı.

## Test ortamı ve sonuçlar

İzinli interpreter:
`/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g41/.venv/bin/python`.
Python **3.13.15**; dağıtım metadata pinleri:
`binance-sdk-derivatives-trading-usds-futures==17.6.0`,
`binance-common==4.5.1`. Yeni G54 testleri owner modülünü veya SDK'yı
import etmez; yalnız açık allowlist owner AST parçalarını izole stub
namespace'te yürütür. Eski SDK contract testleri full suite içinde bu sabit
kurulumun sözleşmesini sınar; özel API/credential bağlantısı kurulmaz.

Yeni fixture, owner çağrısının gerçekten UID501 istediğini kaydeder; yalnız
sentetik dizin üzerinde adapter'ın UID'sini test kullanıcısına eşler. Owner
kaynakta UID501 kaldırılmaz. Negatif UID ve izin kontrolleri ayrıca sınanır.

```sh
/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g41/.venv/bin/python -B -m unittest discover -s tests -p 'test_g54*.py' -v
/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g41/.venv/bin/python -B -m unittest discover -s tests -v
git diff --check
```

| Koşu | Test | Başarılı | Failure | Error | Skip | Süre / çıkış |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Baz, değişiklik öncesi tam suite | 1063 | 1063 | 0 | 0 | 0 | 19.815 s / 0 |
| Nihai G54 hedefli | 87 | 87 | 0 | 0 | 0 | 16.710 s / 0 |
| Nihai tam suite | 1150 | 1145 | 5 | 0 | 0 | 36.384 s / 1 |

Yeni 87 testin **25'i ayrı OS-process testidir**. Bütün belirtilen writer
rolleri aynı gerçek flock altında sınanır. Pipe readiness/command bariyeri
kullanılır; yarış sonuçları sleep tahminine bağlanmaz. Gerçek owner AST'si
ile mutation→WAL sırası, WAL'ın POST/DELETE'den önce bırakılması, intervention
kilidinin hata halinde kapanması, busy durumda sıfır POST/DELETE ve sıfır
WAL retry, timeout/restart sonrası no-resend, iki eşit okuma sonrası geç
ENTRY/incident/reservation ve kapalı marker sınanır. Kısmi dosya/file-fsync
sonrası kesinti yalnız testin başlattığı child süreçlerinde uygulanır.

İlk hedefli koşu 71 test / 1 failure idi: yeni yarış testi, ilk fence dosyası
eşzamanlı yaratılırken bu yönetilen macOS ortamında dönebilen `ENOENT` ret
kodunu hesaba katmıyordu. Bu durum 30 sentetik ad-hoc denemede yeniden
üretildi. Kurulu inode üzerindeki flock/O_EXCL yarışı ile ilk inode yaratma
yarışı ayrı senaryolara ayrıldı. İlk yaratma hatası kalıcı fail-closed ret
olarak kalır; kodda otomatik açma/append/POST retry eklenmedi.

İlk tam koşu 1134 test / 7 failure idi: yukarıdaki yeni yarış testi, aşağıdaki
beş kaynak beklentisi ve eski protective testindeki minimal marker fixture'ı.
Marker'ın şeması parse edilmeden **varlığının yazımı reddetmeye yeterli**
olması sağlandı; marker asla kabul/resolve yetkisi üretmez. Eski protective
testi değiştirilmeden geçti. Sonraki 1142 testlik ara tam koşuda yalnız
aşağıdaki beş failure kaldı. Ara hedefli koşular 74/74 ve 79/79 geçti.

Tam suite'te kalan beş başarısız test / neden:

1. `test_tp_incident_evidence_offline_g50.SourceReferenceTests.test_reference_function_lines_match_read_only_development_source`:
   `actual_order` satırını 564 bekliyor; kaynak değişikliği sonrası 561.
   Fonksiyonun AST davranışı değişmedi. Sabit kaynak referansı güncellenmedi.
2. `test_wal_lock_order_hazards.LockOrderHazardTests.test_current_executor_has_not_wired_experimental_fence`:
   owner'ın adapter import etmemesini bekliyor. G54'ün açıkça istenen
   entegrasyonu bu beklentiyle çelişiyor.
3. `test_wal_shared_fence_audit_offline_g52.StaticAuditTests.test_all_writer_families_are_separate_unproven_rows`:
   eski denetleyici POLICY_RESERVE için doğrudan writable-open arıyor;
   adapter'a taşınan gerçek yazımı takip etmiyor.
4. `test_wal_shared_fence_audit_offline_g52.StaticAuditTests.test_policy_and_incident_are_not_inferred_from_normal_lease`:
   aynı denetleyicinin doğrudan open aramasından dolayı writer sitesi boş.
5. `test_wal_shared_fence_audit_offline_g52.StaticAuditTests.test_readonly_directory_open_is_not_mislabeled_as_wal_writer`:
   eski owner fonksiyonunda bir writable-open ve iki doğrudan fsync çağrısı
   bekliyor; bunlar artık ortak fence modülünde.

Bu testler saklanmadı, skip edilmedi, zayıflatılmadı. Tam doğrulama
**başarılı/tamamlandı olarak sunulmaz**. Görev kapsamı eski audit kaynak ve
testlerini değiştirmeye genişletilmedi. Yeni testler onların bağımsız
incelemesinin yerine geçmez.

Geçici koşu çıktıları:

- `/private/tmp/g54-baseline-tests.log`
- `/private/tmp/g54-targeted-tests.log` — ilk failure dahil
- `/private/tmp/g54-targeted-final-tests.log` — nihai 87 test
- `/private/tmp/g54-full-tests.log` — ilk 7 failure dahil
- `/private/tmp/g54-full-final-tests.log` — nihai tam koşu

## Kalan P0 / kanıt sınırları

1. **T2 tam paket yeşil değil; T4 Claude bağımsız incelemesi yok.** Beş
   kaynak beklentisinin bağımsız değerlendirilmesi ve ilgili audit'in yeni
   transitif çağrı yolunu tanıması ayrı, yetkili değişiklik gerektirir.
2. Flock yalnız katılımcı yazıcıları seri hale getirir. Bu yama belirtilen
   owner ailelerini bağlar; harici/nonparticipant süreçleri veya başka
   modülleri aynı otoriteye bağladığını kanıtlamaz. Kullanıcının sınırladığı
   diğer kaynaklar ve canlı sistem incelenmedi/değiştirilmedi.
3. Kalıcı epoch/generation, ABA engeli, bağımsız replay anchor'ı, WAL suffix
   kaybı ve resolver quiescence kanıtı hâlâ yoktur. Bu nedenle yeni shared
   fence testleri olumlu olsa da çözüm/zero-entry kapıları açılmaz.
4. `LOCK_NB` bekleme/retry eklemez; fakat WAL taraması, fsync, işletim sistemi
   veya disk için sert gecikme üst sınırı kanıtlanmadı. Busy/disk hatası STOP
   gönderildi veya continuous coverage doğrulandı anlamına gelmez. Mevcut
   signed gözetim/alarm yolu korunur; gerçek alarm teslimi bu sentetik
   testlerde kanıtlanmaz. Incident diske yazılamıyorsa bu hata korunur.
5. Child kill ve fsync hata enjeksiyonu fiziksel güç kesintisi/storage
   dayanıklılığı kanıtı değildir. WAL ile policy ledger arasındaki mevcut
   iki adımlı transaction ve restart bütünü için G45 P0 kabulü verilmez.

Sonraki güvenli adım: Claude'un G54 kaynak diff'ini ve yeni testleri bağımsız
incelemesi, hedefli/tam testleri kendisinin çalıştırması ve aşağıdaki alanı
doldurmasıdır. Canlıya taşıma, API/emir, STOP/kilit açma, commit/push/merge/PR
için bu görevden yetki türetilmez; bunların hiçbiri yapılmadı.

## İnceleme için kaynak SHA256

| Dosya | SHA256 |
| --- | --- |
| `owner_single_execution.py` | `d6b869796251a362eb9cc541b488a2603c3c5e6dfb86fbb8e7293772908abeed` |
| `wal_owner_write_adapter_candidate.py` | `ddf894bb8ec0a8ef790604750bff2fa19fea53d228f5d17c4a37da860d314eb9` |
| `wal_writer_fence_candidate.py` | `461096f9e1058f1205489702581327c445e3cbc2e8ed320dc7ca0954a134738a` |
| `tests/test_g54_owner_wal_integration.py` | `80cea42b10c2cad88ec534a8c5c9d7f209631244aaf15e1b14efeadd5e1d7f37` |
| `tests/test_g54_wal_processes.py` | `0049da5df0be37f4b2c5df55f937bedd8ed025138c4cef7a2f6d34c3259812ca` |

## CLAUDE bağımsız son inceleme — BEKLİYOR

- İnceleyen / tarih: **doldurulmadı**
- İncelenen dal / baz / source SHA256 doğrulaması: **bekliyor**
- Writer katılımı ve kilit sırası: **PASS / FAIL / UNPROVEN: bekliyor**
- Signed dispatch, STOP ve no-retry koruması: **bekliyor**
- Incident persist failure sonrası gözetim: **bekliyor**
- Kendi hedefli ve tam test komutları / sonuçları: **bekliyor**
- Beş mevcut audit failure'ının bağımsız değerlendirmesi: **bekliyor**
- G45 P0-01..09 değerlendirmesi / açık bulgular: **bekliyor**
- İnceleme kararı: **verilmedi; canlı/merge onayı değildir**

Ağ, özel Binance API, credential/Keychain/.env, gerçek owner/WAL/canlı
konfigürasyon, servis veya STOP switch üzerinde işlem yapılmadı. Diğer ajan
worktree'lerine kaynak inceleme/değişiklik yapılmadı. Sonuç yalnız G54
geliştirme kopyasında gözden geçirilebilir, commit edilmemiş yamadır.
