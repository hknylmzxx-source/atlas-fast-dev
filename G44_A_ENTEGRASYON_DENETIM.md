# G44-A — G37–G43 OFFLINE sözleşme ve entegrasyon denetimi

Tarih: 10 Kasım 2026. Görev: ATLAS FAST / G44-A / GitHub Issue #9.
Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g44`.
Dal: `codex/g44-integration-audit`.
İncelenen temel ve mevcut HEAD: `79ef10587859b521e09cccc1d1d929902b427c53`.

**Sonuç:** G37–G43, birbirini tamamlayan OFFLINE modellerdir; tamamlanmış bir
owner/STOP/ledger/WAL entegrasyonu oluşturmazlar. İncelenen uygulama
modüllerinde doğrudan bağlanan tek grup G39 → G37/G38'dir. G41, G42 ve G43
bu gruba ve birbirlerine bağlı değildir. Olumlu model sonuçları yeni işlem,
kilit açma veya gerçek STOP güvencesi olarak tüketilemez. Hedefli 328 test
başarılıdır; tam test paketinin başarılı olduğu iddia edilmez.

## 1. Kapsam, yöntem ve teslim sınırı

- Önce bu worktree'deki `AGENTS.md` okundu; dal, HEAD ve temiz başlangıç
  doğrulandı. G37–G43 kaynakları, ilgili mevcut testler ve G37–G43 devir
  belgeleri salt okunur incelendi. Bulgular aşağıdaki kaynak satırlarına ve
  belirtilen testlere dayanır; devir belgelerindeki eski koşular yeni ölçüm
  olarak sunulmaz.
- Yalnız bu yeni rapor oluşturuldu. Mevcut kod/test/devir dosyası ve
  `DEVIR_NOTU.md` değiştirilmedi. Kullanıcının bu göreve özgü tek yeni dosya
  ve commit yasağı nedeniyle devir bilgileri de bu rapordadır. Yeni commit
  kimliği yoktur; yukarıdaki kimlik değişiklik commit'i değil, denetim temelidir.
- G44-B'nin gerçek owner/WAL kaynak çağrı haritasına girilmedi. Gerçek owner
  kaynak kodu incelenmedi/import edilmedi. Gerçek çağıranların varlığı,
  hangi kilitleri hangi sırada aldığı ve üretim yolunun davranışı hakkında
  bu rapordan sonuç çıkarılamaz. G44-B çıktısı alınmadı veya varsayılmadı.
- Ağ/GitHub/Issue okuması, paket kurulumu, gerçek Binance private API/owner
  SDK, credentials/Keychain, canlı dizinler ve diğer worktree'ler kullanılmadı.
  Emir, STOP, owner kilidi, servis veya üretim dosyasında işlem yapılmadı.
  Testlerin yan etkileri kendi geçici OFFLINE fixture'larıyla sınırlıdır.
- Commit/push/merge/PR yapılmadı; Claude veya alt ajan çağrılmadı. Bu rapor
  kod yaması, üretim onayı veya tamamlanmış çapraz inceleme değildir.

Buradaki “eksik entegrasyon”, modelde gizli bir canlı yol bulunduğu anlamına
gelmez. Bağımsız bir sonraki entegrasyon tasarımında karşılanması gereken,
mevcut OFFLINE kanıtın sağlamadığı koşulu ifade eder.

## 2. Mevcut sözleşmeler ve veri akışı

| Bileşen | Girdi ve otorite | Ürettiği sonuç / kalıcılık | Kanıtın sınırı |
| --- | --- | --- | --- |
| G37 TP alarm | `Binding(role, scope, symbol, order_id, qty)`; yapılandırılmış mock kaynağın aynı makbuz nesnesi ve içerik damgası | TP1/TP2 için birer sentetik intent; gözlem/UNKNOWN/karantina; olayla birlikte yerel alarm outbox'ı; gerçek yerel dosya replay'i | Hesap/slot/yön/client/NORMAL-ALGO bağı yoktur. Mock kimliği gerçek provenance değildir. STOP ve owner bağlantısı yoktur. |
| G38 lock preflight | `Binding(scope, account, symbol)`; üç ayrı mock kaynak; mock journal snapshot'ı | Sıfır brüt pozisyon + sıfır normal ve algo açık emir + çapraz bağlı CLOSED ledger/WAL ve SUPHELI incident uyumluysa yalnız `MANUAL_OWNER_REVIEW` | `unlock="NO_UNLOCK"`, `new_entry="NO_NEW_ENTRY"` her durumda sabittir. Snapshot ve generation yalnız bellektedir. |
| G39 offline chain | Aynı dizindeki gerçek G37/G38; frozen `MockStopSnapshot(binding, present)` | G37 telemetrisi ve her raporda yeniden G38 değerlendirmesi; zincir raporu ve yerel alarmlar | STOP yalnız sentetik varlık/bağ gözlemidir. G37 alarmları diskte; G39'un ek alarmları bellektedir. Replay ve belirsizlik giriş yetkisi vermez. |
| G40 timing incelemesi | Devir belgesi ve mevcut beş test; sentetik saat + temp-dir/çocuk süreç fixture'ı | Eski readiness/deadline davranışını karakterize eder | Yeni bir koruyucu yürütücü veya eski koda uygulanmış düzeltme değildir. Bu denetimde G40 testleri çalıştırılmadı. |
| G41 deadline modeli | Tek scope; tek sıralı immutable model zinciri; mock lease, açık `now_s`, contention/fsync girdileri | WAITING → FENCED → MUTATED → DURABLE veya REJECTED/UNKNOWN; soyut WAL olguları | Son kontrol + mutation tek soyut atomik adım varsayılır. Disk, gerçek süreç ortaklığı veya OS atomikliği yoktur. |
| G42 callback adayı | `Intent`, `Lease`, `FenceGrant`; acquire/current/lookup/append/fsync callback'leri | Callback sırasını gerçekten çalıştırır; ACKED/BLOCKED/DEADLINE/UNKNOWN/REPLAY_ONLY; instance içi sonuç cache'i ve scope karantinası | Storage ve lease otoritesi dış sözleşmedir. Check → append atomik değildir. ACKED ve `durability_confirmed` yalnız callback ACK'sidir. |
| G43 F01 iki slot | Ortak `MockSlotStore`, limits/funds, slot/intent/lease/gözlem; tek RLock | En fazla iki aktif slot; brüt risk/marjin rezervasyonu; ENTRY/STOP/TP1/TP2 gözlemleri; mock retirement/restart | Tamamı bellek içidir. RESERVED gerçek giriş izni, MODEL_RETIRED gerçek fon/kilit serbest bırakma değildir. |

Kaynak dayanakları: [G37 kimlik ve makbuz](tp_lifecycle_offline_g37.py#L67),
[G37 günlük](tp_lifecycle_offline_g37.py#L198),
[G38 karar ve değerlendirme](lock_evidence_preflight_offline_g38.py#L168),
[G39 import ve STOP](offline_safety_chain_g39.py#L20),
[G40 teşhis](G40_DEVIR.md#L35),
[G41 model varsayımı](deadline_fence_model_offline_g41.py#L123),
[G42 callback sözleşmesi](protective_deadline_patch_candidate_g42.py#L1),
[G43 store sınırı](f01_two_slot_manager_offline_g43.py#L266).

### 2.1. Fiilen bağlı OFFLINE akış

```text
sentetik TP niyeti / mock makbuz
        → G39 çağrı sarmalayıcısı → G37 EventJournal + TP görünümü + yerel outbox

üç ayrı mock owner okuması + mock ledger/WAL/incident snapshot'ı
        → G39.report() → G38.OfflinePreflight.evaluate()

frozen STOP varlık/bağ gözlemi + yukarıdaki iki sonuç
        → G39 ChainReport: FAIL_CLOSED veya MANUAL_OWNER_REVIEW
        → entry_authorized=false, unlock=false, NO_NEW_ENTRY, NO_RETRY
```

G39 aynı worktree modüllerini doğrular; hazır olumlu `Decision` yerine G38'i
yeniden çağırır. Ancak TP, owner kanıtı ve STOP için ortak atomik snapshot
oluşturmaz. `report()` G39 belleğine alarm da ekler; saf, yan etkisiz bir
okuma değildir. Bir kez raporlanan hata daha sonra kanıt tazelense de aynı
instance'ta kalabilir. [Kaynak](offline_safety_chain_g39.py#L184),
[regresyon](tests/test_offline_safety_chain_g39.py#L197).

G41'in geçişleri, G42'nin callback sonucu ve G43'ün rezervasyonları bu akışa
otomatik aktarılmaz. G37'nin `event_id`/digest'i, G41'in `request_id`'si,
G42'nin `identity`'si ve G38'in `wal_id`'si arasında tanımlı ortak kayıt
kimliği veya nedensellik eşlemesi yoktur. Aynı “WAL” sözcüğü aynı fiziksel
günlüğü ifade etmez: G37 telemetri günlüğü, G38 bellek kaydı, G41 soyut olgu,
G42 ise dış callback sözleşmesidir; G43'te disk WAL bulunmaz.

### 2.2. Kimlik ve tekillik kayıpları

| Sınır | Mevcut kimlik/tekillik | Birleştirmede korunması gereken eksik bağ |
| --- | --- | --- |
| G37 → G39 | TP bağında scope/symbol karşılaştırılır; tracker görünümü yalnız rol ile tutulur | G39 hesabı TP makbuzundan doğrulayamaz. Tek tracker iki slotun dört TP'sini temsil edemez. |
| G38 → G43 | G38 scope/account/symbol ve üç genel okuma türü bilir | Slot, position_side, intent ve order namespace ayrımı yoktur; kapatılmış durum incelemesi ile aktif slot yönetimi ayrı fazlardır. |
| G41 → G43 | Tek model scope'u; WAL'da rol başına tek mutation | İki slotun aynı rolü ve daha sonraki tarihsel slotun aynı rolü ayrıştırılamaz. Request kimliğini değiştirmek rol tekilliğini değiştirmez. |
| G42 → G43 | Instance cache'i ve callback şartı `(scope, role)`; Intent scope/role/identity/symbol/client_id taşır | Account, slot, position_side, NORMAL/ALGO, quantity ve reduce_only taşınmaz. Identity/client değişikliği aynı anahtarda conflict olur. |
| G43 içi | Slot id + scope/account/symbol/position_side; tam intent; client hesap genelinde, order `(account, order_kind)` içinde tekil | Bu güçlü model bağı önceki modellere daraltılırken kaybolmamalıdır. Slot id OrderIntent içinde değil, dış slot kaydındadır. |

Kaynaklar: [G37 rol indeksi](tp_lifecycle_offline_g37.py#L395),
[G39 TP eşleşmesi](offline_safety_chain_g39.py#L167),
[G41 rol tekilliği](deadline_fence_model_offline_g41.py#L299),
[G42 cache anahtarı](protective_deadline_patch_candidate_g42.py#L183),
[G43 kimlik alanları](f01_two_slot_manager_offline_g43.py#L78),
[G43 namespace kontrolü](f01_two_slot_manager_offline_g43.py#L346).

**Çalıştırılan bellek içi karşı örnek:** G43'ün mevcut `demo()` sonucu iki
aktif slotu aynı `MOCK_SCOPE` altında tutar. İki slot için farklı symbol,
identity ve client id taşıyan TP1 intent'leri, mevcut G42 testinin
`ManualClock`/`MockStorage` fixture'ı üzerinden aynı candidate'a daraltılarak
verildi. Sonuçlar sırasıyla `ACKED/MOCK_CALLBACK_ACK_ONLY` ve
`BLOCKED/IDEMPOTENCY_CONFLICT`; mock storage'da yalnız bir kayıt oluştu.
Bu, mevcut bir üretim çağrısında hata bulunduğu iddiası değildir; doğrudan
scope/rol projeksiyonunun iki-slot sözleşmesini karşılamadığının örneğidir.
Farklı instance kullanmak da paylaşılan storage anahtarı sorununu çözmez.

Gelecekteki OFFLINE adaptör taslağı en az sürümlü şema, iş scope'u,
account/symbol/position_side, slot kimliği ve yaşam döngüsü kimliği, rol,
client/order kimliği ve NORMAL/ALGO ad alanı, miktar/reduce_only,
request/event/ledger/WAL/incident ilişkisi ile epoch/generation taşımalıdır.
Kaynak/provenance sınıfı, sıra, gözlem zamanı ve saat alanı ayrıca açık olmalı;
bilinmeyen alan güvenilir varsayılanla doldurulmamalıdır. Slotu ayırmak için
scope metnini değiştirmek, ortak owner/fence veya brüt bütçe sınırını
parçalayamaz. Bu öneri uygulanmış bir şema veya patch değildir.

## 3. Uyumluluk ve çakışmalar

### 3.1. Ortak güvenli davranış

Olumlu sonuçların yetki vermemesi; belirsiz mutation sonrasında “yazılmadı”
çıkarılmaması; TP başarısından STOP kanıtı üretilmemesi; kısmi dolumun kesin
dolum miktarını geriletmemesi ve restart sonrasında otomatik yeniden
başlatmanın kapalı tutulması modellerin ortak yönleridir. Bunlar OFFLINE
karar/fixture davranışlarıdır; gerçek tüketicinin bu kararları doğru
uyguladığının kanıtı değildir.

### 3.2. Deadline, saat ve soyut atomiklik

- G40 mevcut eski döngüde, sleep sonrasında kilit boşalırsa deadline geçmiş
  olmasına rağmen ikinci append'in başarılı olabildiğini karakterize eder.
  Readiness mesajı da kilidin hâlâ tutulduğunu kanıtlamaz. İki karakterizasyon
  testinin yeşil olması bu risklerin giderildiği anlamına gelmez.
  [G40 testleri](tests/test_clock_fixture_offline_g40.py#L137).
- G41 ve G42 pozitif timeout için her yeni denemeden önce ve fence altında
  yeniden kontrol uygular; `now == deadline` dolmuş süredir. G42 bu
  davranışı yeni adayda çalıştırır, G40'ın eski uygulamasını değiştirmez.
- **Sıfır timeout eşdeğer değildir.** G41 acquisition ve mutation'ın aynı
  başlangıç model anında olmasını ister; G42 tek nonblocking acquisition,
  sıfır sleep/retry uygular, elapsed-time deadline uygulamaz. G42 testinde
  saat 100 → 101 → 102 ilerlerken lease geçerliyse ACKED dönebilir.
  Bu bilinçli sözleşme ayrımıdır; G42, G41'in tam davranışsal eşdeğeri sayılamaz.
  [G41 testleri](tests/test_deadline_fence_model_offline_g41.py#L211),
  [G42 testi](tests/test_protective_deadline_patch_candidate_g42.py#L239).
- **G41'in atomik model adımı G42'de fiziksel atomiklik değildir.** Son
  kontrol sonrasında append callback'i saati veya generation'ı değiştirebilir.
  Mevcut iki testte mock kayıt gerçekten oluşur, son kontrol UNKNOWN verir;
  kayıt geri alınmaz. Bu testlerin geçmesi “geç/stale yazım imkânsız” kanıtı
  değildir. [Sınır testleri](tests/test_protective_deadline_patch_candidate_g42.py#L582).
- G37 varsayılanında duvar saati milisaniyesi; G38 caller'ın `now_ms`'i;
  G41 açık sentetik saniye; G42 varsayılanında monotonic saniye; G43 açık
  sentetik tam sayı milisaniyesi kullanır. Saat başlangıcı/alanı, birim,
  tolerans ve yeniden başlatma davranışı ortaklaştırılmamıştır. Yalnız
  1000 ile çarpmak/bölmek saat alanı farkını çözmez. G42 örneklenen saatin
  ilerlemesi ve callback'lerin dönmesi de dış varsayımlardır; sert dönüş
  süresi veya deadline içinde fsync garantisi yoktur.

### 3.3. Preflight, STOP ve aktif slot farklı fazlardır

G38'in olumlu incelemesi sıfır pozisyon, sıfır normal/algo açık emir ve
CLOSED ledger/WAL ister; incident SUPHELI kalır. Bu, açık ENTRY/STOP/TP
izleyen G43 aktif-slot safhasına genel “hazır” kapısı olarak takılamaz.
G39'un olumlu fixture'ı da iki TP FILLED, bu kapalı owner kanıtı ve
`STOP.present=True` içerir. Boolean STOP varlığının tarihsel gözlem mi,
halen açık koruyucu emir mi olduğu tanımlı değildir. Gerçekten aktif STOP
olduğu yorumu, sıfır açık algo/normal emir kanıtıyla ayrıca uzlaştırılmalıdır;
fixture gerçek borsa durumunun tutarlılığını kanıtlamaz.

G39 STOP snapshot'ı order/client kimliği, ad alanı, quantity, reduce_only,
position_side, status ve zaman taşımaz. G41 snapshot'ı yalnız present taşır;
G42'nin kendi STOP gözlemi arabirimi yoktur, test fixture'ındaki nesnenin
değişmediği kontrol edilir. G43 daha ayrıntılı mock miktar/güncellik hesabı
yapar, ancak o da gerçek koruma kanıtı üretmez.

G43'te TP UNKNOWN/MISSING geçmişi yeni koruyucu intent rezervasyonunu da
engeller. G42'de UNKNOWN scope'u bütün rollere kapatır. Bu kapalı davranış
izinsiz retry'yi önler; STOP izleme ve tehlike bildiriminin yürütme/retry
kapısından bağımsız sürmesi gerekir. Mevcut STOP'u izlemek ile yeni STOP
onarımı/emri üretmek ayrı yetki ve akışlardır; modeller ikincisini uygulamaz.
[G43 belirsizlik kapısı](f01_two_slot_manager_offline_g43.py#L436),
[STOP/TP bağımsızlığı testi](tests/test_f01_two_slot_manager_offline_g43.py#L462).

### 3.4. Gözlem, alarm ve sonuç tüketimi

- G37 aynı makbuz tekrarında TP'yi karantinaya alır; timestamp ve sequence
  kesin artmalıdır, terminal durumdan sonraki gözlem reddedilir. G43 tam
  tekrar gözlemi dolumu yeniden uygulamadan DENY eder; yeni sequence ile aynı
  timestamp kabul edilebilir, aynı terminal status/dolumun güncel teyidi
  kabul edilebilir. Reddedilen G43 gözlemi otomatik olarak kalıcı uncertainty
  yazmaz. Ortak adaptör, duplicate/conflict ayrımını ve hangi hatanın alarm
  olarak kalacağını açıkça belirlemelidir.
- G37 TP miktarını bilir, ENTRY dolumuyla TP1+TP2/STOP toplamını bağlamaz.
  G43 koruyucu toplam dolumun bilinen ENTRY dolumunu aşmasını reddeder.
  TP gözlemi ENTRY'den önce ulaşırsa, bağımsız olarak doğru bir TP verisi bile
  bu modelde reddedilebilir. Ortak snapshot/sıralama ve reddedilen kanıtın
  kaybolmadan incelemeye aktarılması için entegrasyon protokolü yoktur.
  [G43 gözlem yolu](f01_two_slot_manager_offline_g43.py#L458).
- G37 PARTIAL → FILLED sonrasında eski RECONCILE alarmını diskte korur;
  G39 bu geçmiş nedeniyle kapalı kalır. G43 alarmları raporda yeniden
  hesaplar: UNKNOWN/MISSING işareti kalıcı bellek bayrağıdır, PARTIAL/stale
  gibi geçici alarmların G37 ile aynı tarihsel outbox'ı yoktur. Bir raporu
  diğeriyle değiştirmek alarm geçmişini koruyan bir entegrasyon değildir.
  [G37 testi](tests/test_tp_lifecycle_offline_g37.py#L88),
  [G43 rapor hesabı](f01_two_slot_manager_offline_g43.py#L572).
- G37/G38 yaş sınırı varsayılan 5000 ms; G43 varsayılanı 1000 ms'dir.
  G37 `report()` son TP durumunun yaşını yeniden değerlendirmez; G39 G38'i
  tazelik açısından tekrar değerlendirir, fakat TP durumu ve frozen STOP
  için ortak güncel gözlem sağlamaz. G43 her raporda STOP/TP yaşını hesaplar.
- **Yetki alanları tip olarak aynı değildir.** G38 `unlock` alanı string
  `"NO_UNLOCK"`'tur; Python'da `bool("NO_UNLOCK")` True'dur. Diğer modellerde
  çoğunlukla boolean false vardır; G37 raporunda unlock alanı bulunmaz.
  Bu denetimde G38 string tipi ve truthiness değeri ayrıca gözlendi.
  `if decision.unlock` veya eksik alan için izin veren varsayılan kullanan
  bir tüketici tasarlanamaz. Böyle hatalı bir gerçek tüketici bulunduğu
  iddia edilmiyor. Sabit tipli, kapalı sonuç eşlemesi gerekir.
- G42 aynı intent için cached ACKED döndürürken yeni lease/deadline kontrolü
  yapmaz; bu tarihsel işlemin sonucudur. Başka instance eşit kayıt görürse
  geçmiş durability'yi bilemeyip UNKNOWN verir. ACKED/DURABLE/RESERVED/
  MANUAL_OWNER_REVIEW aynı başarı veya yeni yürütme izni enum'u değildir.
  [G42 cache](protective_deadline_patch_candidate_g42.py#L181),
  [mevcut kayıt yolu](protective_deadline_patch_candidate_g42.py#L265).

## 4. STOP / owner / ledger / WAL bağlantı önkoşulları

| Sınır | Mevcut OFFLINE kanıt | Bağlantıdan önce gerekli, burada kanıtlanmamış koşul |
| --- | --- | --- |
| Owner okuması | G37/G38 mock nesne kaydı; G43 typed mock gözlem | Kaynağın hesap/ürün/pozisyon modu ve intent'e bağlanması; tüm normal/algo kanıtlarının eksiksizliği; zaman/sıra ve tutarlılık sınırı. `signed=True` veya makbuz kopyası yeterli değildir. |
| STOP | G39/G41 immutable snapshot; G43 mock aktiflik/kalan miktar/güncellik | Tam order namespace/kimlik, account/symbol/position_side, gerçek status ve korunan brüt miktar; güncel gözlemin aynı pozisyon yaşam döngüsüne aitliği. WAL etiketi STOP kanıtının yerine geçemez. |
| Ledger/WAL/incident | G38'de üç record id karşılıklı bağlı; CLOSED/CLOSED/SUPHELI ve INTACT | Gerçek kayıt şemasına kayıpsız eşleme, ortak snapshot/nesil, durable kapanış ve incident geçmişi. SUPHELI'nin model kararıyla çözülmemesi; son kontrolden sonraki değişimin yetkiye dönüşmemesi. |
| Fence/lease | G41 tek sıralı otorite; G42 callback grant/current şartı; G43 ortak bellek mutex'i | Bütün yazarların katıldığı otorite; kalıcı epoch/generation; doğru scope/slot kilit kapsamı; kontrol ve storage mutation için ortak linearization noktası. Gerçek kilit sırası G44-B'nin alanıdır. |
| Intent ve recovery | G37 disk replay; G41/G43 soyut/bellek restart; G42 dış replay-only gereği | Intent, olası gönderim, UNKNOWN, durum kanıtı ve bütçenin ortak nedensel kaydı; boş disk/store'un “önceki işlem yok” sayılmaması; restart'ta yeni izin üretmeyen kalıcı recovery kapısı. |
| Risk/fon | G43 RLock altında statik mock limits/funds ve caller risk girdisi | Yetkili risk/fon kaynağı, güncellik ve hesap izolasyonu; gerçek risk hesabıyla uyum; rezervasyon ve durable intent'in birlikte ele alınması. Mevcut risk limitleri bu raporda değiştirilmez. |

G38 üç okumanın her birinin yaşını kontrol eder; bunların aynı borsa anına
ait olduğunu veya aralarında durum değişmediğini gösteren ortak snapshot
kanıtı yoktur. Journal generation kontrolü exchange okumalarını kapsamına
almaz. G38'in disk descriptor/symlink/izin/tutarlılık protokolü de uygulama
değil, önceki devir belgesinde tasarım sınırı olarak açıklanmıştır.
[G38 journal ve son kontrol](lock_evidence_preflight_offline_g38.py#L206).

G44-B ile daha sonra karşılaştırılabilecek girdi, yalnız bu sınırların
eşleme tablosudur: gerçek caller/okuyucu/yazıcı, aldığı/verdiği alanlar,
koruduğu kilit kapsamı, hata/UNKNOWN dönüşü ve crash noktası. Bu rapor bunları
gerçek kodda aramaz; G44-B yerine çağrı haritası veya birleşik onay üretmez.

## 5. Önceliklendirilmiş eksik gerçek entegrasyon listesi

P0: Bir gün yetkili gerçek akışa bağlama düşünülmeden önce kapatılması gereken
veri bütünlüğü/güvenlik engeli. P1: Entegrasyon sözleşmesi ve doğrulama engeli.
P2: Ölçek, bakım ve raporlanabilirlik işi. Bu sıralama mevcut OFFLINE kodda
canlı aksiyon yapılmasını veya bu işlerin bu görevde uygulanmasını önermez.

| Kimlik | Eksik / etkisi | Kapanış için gerekli kanıt |
| --- | --- | --- |
| **P0-01** | Ortak kimlik ve iki-slot namespace'i yok; G37 rol indeksi ve G41/G42 scope/rol tekilliği iki slotu ve tarihsel yeni slotu ayıramıyor | İki hesap, aynı sembolde LONG/SHORT, NORMAL/ALGO aynı id metni, iki eşzamanlı TP1/STOP ve emekli slot sonrası yeni yaşam döngüsü için kayıpsız eşleme; parent scope/fence/bütçe izolasyonu korunmalı. |
| **P0-02** | Son lease/deadline kontrolü ile mutation aynı gerçek atomik işlem değil; bütün yazarları kapsayan kalıcı fence otoritesi kanıtlanmadı | OFFLINE storage fixture'ında gerçek ayrı süreçler, generation/ABA değişimi ve check sonrası preemption altında tekillik; geç/stale mutation'ın kabul kuralı ve UNKNOWN davranışı; G41 varsayımı ile G42 sınırının açık eşlemesi. |
| **P0-03** | Kalıcı ortak recovery/karantina/bütçe otoritesi yok; G42 cache ve G43 store süreç ölümünde kaybolur | Append/fsync/ACK sınırlarının her iki yanında crash; boş veya kısmi replay; eski lease; UNKNOWN ve tarihsel kimliklerin korunması; restart sonrası otomatik yeni intent/bütçe iadesi olmaması. G37 telemetri replay'i tek başına yeterli değil. |
| **P0-04** | Gerçek STOP kanıtı ve pozisyon/owner/ledger/WAL ilişkisinin ortak tutarlılığı yok | Bağımsız güncel STOP ve normal/algo/pozisyon kanıtlarının tam kimlikle uzlaştırılması; açık slot/kapalı inceleme fazlarının ayrılması; TP başarılı/UNKNOWN iken STOP tehlikesinin görünür kalması. Hiçbir boolean veya WAL marker'ı bu kanıtı ikame edemez. |
| **P0-05** | Gerçek global risk/fon rezervasyonu ile intent ve kayıt yaşam döngüsünün atomik bağı yok; G43 fiyat/fon/risk sentetik | İki ayrı süreçte son fon/son slot yarışı, kısmi/UNKNOWN/restart ve slot retirement sonrasında brüt limitlerin korunması; yetkili güncel girdiler ve mevcut risk politikasıyla bağımsız eşleme. Yeni limit belirlenmez. |
| **P0-06** | Ortak kapalı sonuç tüketimi yok; string/boolean farkı, cached ACK ve RESERVED gibi durumlar yetkiyle karıştırılabilir | Sürümlü, tip denetimli sonuç sözleşmesi; bilinmeyen/eksik alan ve enum'un reddi; her olumlu model sonucunda entry/unlock/STOP yetkisi oluşmadığını gösteren OFFLINE tüketici kanıtı. |
| **P1-01** | G41/G42 sıfır timeout sözleşmeleri farklı; G40'ın eski retry penceresine uygulanmış düzeltme yok; saat alanları ortak değil | Sıfırın anlamı, pozitif budget ve expiry eşitlikleri, birimler ve saat alanı için tek kabul metni; oversleep/late-release/generation/fsync senaryolarının bu metne göre değerlendirilmesi. |
| **P1-02** | Gözlem normalizasyonu/sırası ve alarm geçmişi aynı değil; geç gelen TP verisi reddedilip kaybolabilir | Duplicate ile conflict ayrımı, aynı timestamp/terminal teyidi politikası, ENTRY/STOP/TP toplam dolum snapshot'ı; reddedilen kanıtın yetki vermeden incelemeye taşınması; G37 tarihsel alarmının G43 raporuyla silinmemesi. |
| **P1-03** | Dış alarm teslimi ve kalıcı tüketici ACK'si yok; G39/G43 ek alarmları bellek/rapor düzeyinde | Hassas veri içermeyen ortak olay kimliği; kalıcı outbox ve idempotent tüketim; crash/tekrar teslimde alarm kaybı veya otomatik incident temizliği olmaması. Bu görevde bildirim gönderilmez. |
| **P1-04** | G37–G43'ü birlikte, iki slot ve sekiz rol üzerinden sınayan mevcut uçtan uca OFFLINE test yok | G37/G38/G39 ile deadline/callback/slot modelini birlikte sınayan, yetkiler kapalı kalan ayrı fixture; kimlik çakışması, iki-slot yarışları, stale STOP, TP UNKNOWN, yarım journal ve replay kapsanmalı. Mevcut yerel başarıların toplamı bu testin yerine geçmez. |
| **P1-05** | Tam doğrulama ve bağımsız çapraz inceleme açık; SDK bağımlılıkları yok | Bu raporun kanıtlarıyla bağımsız kaynak/test incelemesi; daha sonra ayrıca yetkilendirilmiş izole bağımlılık ortamında tam test sonucu. Eksik SDK veya G40 timing sorunu skip/gevşetmeyle kapatılamaz. |
| **P2-01** | G37 her append'de tüm günlüğü okur; G43 tarihsel kimlik/store ve mock kaynak kayıtları sınırsız büyür | Replay/tekillik/karantina kanıtlarını koruyan ölçülmüş kapasite ve saklama tasarımı; harici bütünlük kökü olmadan WORM/truncation tespit iddiası olmaması. Bu denetimde eski kod veya kayıt silinmez. |
| **P2-02** | Modeller arası sürümlü şema, neden-kodu eşlemesi ve kalıcılık sınıfları yok | MOCK kimliği, yerel journal olgusu, callback ACK'si ve gerçek kanıtın ayrı etiketleri; tarihsel sonuç/güncel kanıt ayrımı; her kararın temel/şema/test sürümüyle izlenebilmesi. |

## 6. Kanıtlanmamış güvenlik varsayımları

1. Güvenilen Python süreci, kaynak nesnesi ve callback'ler varsayılır.
   Frozen dataclass, nesne kimliği, SHA256 ve `lease_is_current=True`
   düşmanca kod veya yanlış otorite karşısında bağımsız güven kökü değildir.
   G42 keyfi callback'i canlı erişimden ayıran bir sandbox sağlamaz.
2. Mock okumanın gerçek hesaba aitliği, doğruluğu ve eksiksizliği kanıtlanmaz.
   Bir kaynağın kendi verdiği nesneyi tanıması, borsa provenance'ı değildir.
3. G37'nin POSIX fsync/link/flock protokolü ve testleri, fiziksel güç kesintisi,
   farklı dosya sistemi davranışı veya disk sahibine karşı WORM kanıtı değildir.
   Dış kök olmadan geçerli son kayıtların silinmesi/tüm zincirin yeniden
   yazılması güvenilir biçimde dışlanamaz.
4. G41 eski immutable snapshot'tan iki ayrı model zinciri türetilmesini
   fiziksel olarak engellemez. Tek sıralı otorite harness varsayımıdır;
   mevcut fork testi bunu açıkça gösterir.
   [Test](tests/test_deadline_fence_model_offline_g41.py#L459).
5. G42'de bütün yazarların aynı fence'e katıldığı, acquire'ın nonblocking,
   lookup'ın salt okunur ve append_once'ın storage düzeyinde tekil olduğu
   dış şartlardır. Fsync ACK enum'u fiziksel durability'yi kanıtlamaz.
6. Deadline/lease kontrolünden sonraki preemption ve bloklanan callback
   kesilmez. UNKNOWN sonucu geç yazımı geri almaz. `durability_confirmed=False`
   kayıt yokluğu; True ise gerçek STOP varlığı anlamına gelmez.
7. G43 ortak store'u paylaşmayan iki OS sürecini koordine etmez. Thread
   yarışları gerçek, “iki süreç” facade testleri simülasyondur. Yeni boş
   store gerçek recovery veya flat hesap kanıtı değildir.
8. G43 risk/fon/fiyat girdilerinin yetkili ve güncel olduğu; gerçek ücret,
   slippage, PnL, marjin/venue ve reduce-only kurallarıyla uyum kanıtlanmaz.
9. Ortak saat alanı, snapshot nesli ve tüm modülleri kapsayan atomik durum
   yoktur. Tek tek fresh okumalar birlikte tutarlı pozisyon/STOP durumu
   oluşturmuş sayılmaz.
10. G37 yerel outbox'ının dış owner'a teslimi, G39/G43 alarm geçmişinin süreç
    ölümü sonrası kalması ve herhangi bir incident'in güvenli çözümü
    kanıtlanmaz. Hiçbir model kararı owner kilidi temizleme onayı değildir.
11. Seçilen testlerin başarısı gerçek owner çağıranlarının aynı sözleşmeye
    uyduğunu veya üretim sisteminin güvenli olduğunu kanıtlamaz. Bu kısım
    G44-B'nin ayrı kanıtları ve daha sonraki bağımsız değerlendirme olmadan açıktır.

## 7. Bu denetimde çalıştırılan doğrulamalar

Mevcut altı test dosyası, worktree kökünde `python3 -B` altında stdlib
`unittest.TestLoader().discover(..., pattern=...)` ile aşağıdaki sırada
çalıştırıldı; her dosya için ayrı `TextTestRunner(verbosity=2)` sonucu alındı.
Bir hata olduğunda toplam komutun başarısız çıkması sağlandı; koşunun çıkışı 0.

| Mevcut test dosyası | Test | Başarılı | Failure / error / skip | Süre |
| --- | ---: | ---: | ---: | ---: |
| `tests/test_tp_lifecycle_offline_g37.py` | 47 | 47 | 0 / 0 / 0 | 1.769 s |
| `tests/test_lock_evidence_preflight_offline_g38.py` | 47 | 47 | 0 / 0 / 0 | 0.039 s |
| `tests/test_offline_safety_chain_g39.py` | 52 | 52 | 0 / 0 / 0 | 5.577 s |
| `tests/test_deadline_fence_model_offline_g41.py` | 45 | 45 | 0 / 0 / 0 | 0.025 s |
| `tests/test_protective_deadline_patch_candidate_g42.py` | 54 | 54 | 0 / 0 / 0 | 0.259 s |
| `tests/test_f01_two_slot_manager_offline_g43.py` | 83 | 83 | 0 / 0 / 0 | 0.088 s |
| **Toplam** | **328** | **328** | **0 / 0 / 0** | **7.757 s** |

Tam çıktı: `/private/tmp/g44-a-targeted-fipdi4cn.log`.
Bu yerel geçici log commit edilmedi; kalıcı teslimin parçası değildir.
Her dosya bağımsız olarak şu komut biçimiyle yeniden çalıştırılabilir:

```sh
python3 -B -m unittest discover -s tests -p test_tp_lifecycle_offline_g37.py -v
python3 -B -m unittest discover -s tests -p test_lock_evidence_preflight_offline_g38.py -v
python3 -B -m unittest discover -s tests -p test_offline_safety_chain_g39.py -v
python3 -B -m unittest discover -s tests -p test_deadline_fence_model_offline_g41.py -v
python3 -B -m unittest discover -s tests -p test_protective_deadline_patch_candidate_g42.py -v
python3 -B -m unittest discover -s tests -p test_f01_two_slot_manager_offline_g43.py -v
```

Ek olarak mevcut demo/test fixture'larıyla, dosya üretmeyen tek bellek içi
denemede bölüm 2.2'deki iki-slot/G42 çakışması, G38 NO_UNLOCK string'inin
truthiness'i ve G37 Binding içinde account alanının yokluğu gözlendi.
Bu deneme yeni test dosyası değildir ve 328 test sayısına dahil edilmedi.

G40 için `G40_DEVIR.md` ve `tests/test_clock_fixture_offline_g40.py` statik
incelendi; eski WAL bağımlı testleri bu dar koşuya eklenmedi. Tam
`python3 -B -m unittest discover -s tests -v` bu denetimde çalıştırılmadı;
gerçek SDK importuna başvuran mevcut testler yürütülmedi.

**Önceki iki SDK hatası açıkça korunuyor:**

- `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
  önceki tam koşularda `ModuleNotFoundError`.
- `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
  önceki tam koşularda `PackageNotFoundError`.

Kaynak: [G43'ün son tam koşu kaydı](G43_DEVIR.md#L168): 673 test / 671
başarılı / 2 hata / 0 assertion failure / 0 skip, çıkış 1. Bu tarihsel
sonuç bu denetimde tekrarlanmış gibi sunulmaz. Bu oturumda yalnız stdlib
metadata ve üst düzey `find_spec` ile
`binance-sdk-derivatives-trading-usds-futures` ve `binance-common` için
metadata ve import spec bulunmadığı doğrulandı; SDK modülü import edilmedi.
Paket kurulmadı, sahte paket/metadata eklenmedi, test değiştirilmedi veya
skip edilmedi. Tam paket yeşil kabul edilmez; G40'taki timing riski de
mevcut iki bağımlılık hatasına indirgenemez.

## 8. Claude'un son bağımsız incelemesi için girdi listesi

Claude bu oturumda çağrılmadı. Aşağıdaki liste daha sonraki bağımsız
incelemeye girdidir; inceleme yapılmış veya onay verilmiş sayılmaz.

| Grup | İncelenecek mevcut kaynak | Bağımsız test / önceki bağlam |
| --- | --- | --- |
| G37 | `tp_lifecycle_offline_g37.py` | `tests/test_tp_lifecycle_offline_g37.py`, `G37_DEVIR.md` |
| G38 | `lock_evidence_preflight_offline_g38.py` | `tests/test_lock_evidence_preflight_offline_g38.py`, `G38_DEVIR.md` |
| G39 | `offline_safety_chain_g39.py` | `tests/test_offline_safety_chain_g39.py`, `G39_DEVIR.md` |
| G40 | `G40_DEVIR.md` içindeki timing teşhisi ve kanıt sınırları | `tests/test_clock_fixture_offline_g40.py`; bu raporda statik inceleme |
| G41 | `deadline_fence_model_offline_g41.py` | `tests/test_deadline_fence_model_offline_g41.py`, `G41_DEVIR.md` |
| G42 | `protective_deadline_patch_candidate_g42.py` | `tests/test_protective_deadline_patch_candidate_g42.py`, `G42_DEVIR.md` |
| G43 | `f01_two_slot_manager_offline_g43.py` | `tests/test_f01_two_slot_manager_offline_g43.py`, `G43_DEVIR.md` |
| Denetim | Bu rapor; temel `79ef10587859b521e09cccc1d1d929902b427c53` ve dal bilgisi | Bölüm 7 test komutları/sonuçları; mevcut dosyalarda değişiklik olmadığına ilişkin Git kontrolü |

İnceleyenin bağımsız olarak değerlendirmesi gerekenler:

1. G39 → G37/G38 bağının gerçekten çalıştığı ve diğer modellerle ortak
   entegrasyon varmış gibi yorumlanmadığı; sabit yasak alanlarının bütün
   olumlu/olumsuz sonuçlarda korunması.
2. P0-01 kimlik kaybı ve iki-slot çakışmasının kapsamı; G43'ün client/order
   ad alanları, LONG/SHORT/BOTH ayrımı ve emekli kimlik geçmişi.
3. G41/G42 sıfır timeout farkı, callback içi geç yazım/generation testleri ve
   immutable model fork sınırı; soyut varsayımın gerçek garanti sayılmaması.
4. CLOSED/CLOSED/SUPHELI ile aktif slot/STOP safhalarının ayrımı; tek tek
   fresh owner kanıtlarının ortak tutarlılık sağlamaması; TP'den STOP
   üretilmemesi ve UNKNOWN'un retry/onarım iznine dönüşmemesi.
5. G37 kalıcı alarm geçmişi ile G39 bellek latch'i ve G43 hesaplanan
   alarmların farkı; cached ACK'nin tarihsel sonuç olarak tüketilmesi;
   string NO_UNLOCK için truthiness tehlikesi.
6. İki gerçek thread, iki facade ve iki OS süreci kanıtlarının ayrılması;
   crash/replay/fon rezervasyonu ve bütün-yazar fence varsayımlarının açık kalması.
7. Kaynak ve testleri kendi değerlendirmesiyle inceleyip kapsamı izin verilen
   OFFLINE testleri kendisinin çalıştırması; tam paket çalıştırılacaksa SDK
   hataları ile timing başarısızlığını ayrı raporlaması. Test/SDK kurulum
   yetkisi bu rapor tarafından genişletilmez.
8. G44-B raporu daha sonra kullanıcı tarafından getirildiğinde sınır tablosunu
   onun gerçek çağrı haritasıyla karşılaştırması; incelemediği owner/üretim
   yolu için onay vermemesi. Bu oturumun çıktısında G44-B yerine geçecek sonuç yoktur.

## 9. Devir durumu ve sonraki güvenli adım

Tamamlanan iş, bu temel üzerinde G37–G43 sözleşme/kimlik/veri akışı ve
entegrasyon boşluklarının denetlenmesi ile bu yeni rapordur. Değişen mevcut
dosya yoktur; commit kimliği üretilmedi. Hedefli doğrulama 328/328 başarılı;
tam doğrulama ve bağımsız inceleme açıktır. P0/P1/P2 maddeleri uygulanmış
düzeltme değil, sonraki işlerin kabul koşullarıdır.

Sonraki güvenli adım, kullanıcının ayrı G44-B çıktısıyla bu raporu son
bağımsız incelemeye sunması ve önce yalnız OFFLINE ortak sözleşme/fixture
tasarımının değerlendirilmesidir. Bu raporun teslimi için ek kullanıcı
onayı gerekmiyor. Mevcut/kritik kodu değiştirmek, canlı yetki/risk/kilit
davranışına müdahale etmek veya üretime bağlamak bu görevin kapsamı ve
onayı içinde değildir. Hiçbir merge/patch/PR veya canlı sonraki adım başlatılmadı.
