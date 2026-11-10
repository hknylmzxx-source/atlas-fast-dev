# G43 / F01 / GitHub Issue #8 — İki işlem-slotunun offline modeli

Tarih: 10 Kasım 2026.
Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g43`.
Dal: `codex/g43-f01-two-slots`.
Başlangıç ve teslim HEAD: `ff461a3928ebaa9abf9ec105994e61ee08a0bd44` (G42).
G43 commit kimliği yoktur; kullanıcı talimatıyla commit/push/merge/PR yapılmadı.

**Yeni hedefli testler: 83/83 başarılı. Son tam paket: 673 test, 671 başarılı,
2 mevcut SDK hatası, 0 assertion başarısızlığı, 0 atlama; çıkış 1. Tam paket
başarılı değildir. Geliştirme canlı FAST'e bağlanmadı. CLAUDE SONRA.**

## Dosyalar ve çalışma kapsamı

Yalnız üç yeni dosya:

- `f01_two_slot_manager_offline_g43.py`: standart kütüphane kullanan standalone
  iki-slot yöneticisi, mock bellek otoritesi, gözlemler, raporlar ve çalışan demo.
- `tests/test_f01_two_slot_manager_offline_g43.py`: 83 yeni unittest; ayrıca
  alt senaryolar için subTest kullanıldı. Mevcut testlere dokunulmadı.
- `G43_DEVIR.md`: bu Türkçe sözleşme, test kaydı, riskler ve devir.

Önce `AGENTS.md` okundu. Bu worktree'deki G37–G42 offline kaynak/test/devir
örnekleri yalnız okundu; yeni modül bunları veya owner/STOP/guard/risk
modüllerini import etmez. G43'ün mevcut akışlara import/çağrı bağlantısı yoktur.
Kritik kaynaklar dahil hiçbir önceden izlenen dosyada değişiklik yoktur.
Kullanıcının üç dosya sınırı nedeniyle ayrıca `DEVIR_NOTU.md` yazılmadı.

Ağ/GitHub Issue okuması, gerçek Binance API, private/signed GET, owner SDK,
Keychain/credentials, canlı sistem dizinleri ve diğer worktree'ler kullanılmadı.
Paket kurulmadı; başka `.venv` kopyalanmadı. Servis, çalışan bot, emir,
STOP veya güvenlik kilidi üzerinde aksiyon yoktur. Mevcut tam test paketinin
offline fixture'ları çalıştırıldı; eksik SDK'yı import etmeye çalışan iki
mevcut test aşağıda açıkça raporlandı.

## Çalışan modelin sözleşmesi

`MockLimits` ve `MockFunds` sabit mock girdilerdir. `MockSlotStore` kayıt,
lease nesilleri, epoch ve sentetik saatin tek bellek otoritesidir.
`TwoSlotManagerOffline(store)` bu otoriteyi kullanan istemcidir. Birden çok
istemci aynı store'u paylaşmalıdır. Dış callback, endpoint veya yürütücü
arabirimi yoktur. `RESERVED`, yalnız sentetik intent ve kaynak rezervasyonu
kaydını ifade eder; gerçek işlem kabulü üretmez.

Her `Decision`, `Report`, `SlotView` ve demo üst sonucu şu sabit alanları taşır:

```text
mode = OFFLINE_MOCK_ONLY
entry_authorized = false
unlock = false
retry_policy = NO_RETRY
```

Alanlar frozen dataclass üzerinde `init=False` tanımlıdır. Olumlu mock
rezervasyon/gözlem/bütçe iadesi de yetki vermez. Düşmanca Python koduna karşı
frozen nesneler veya mock lease kimliği bir güvenlik sınırı değildir.

### Kimlik ve iki-slot sınırı

- Slot bağı: `slot_id`, `scope`, `account`, `symbol`, `position_side`.
  `LONG`, `SHORT`, `BOTH` yalnız model etiketleridir.
- Her `ENTRY`, `STOP`, `TP1`, `TP2` intent'i tam bağını, rolünü, client id,
  order id, `NORMAL`/`ALGO` ad alanını, miktarını ve reduce-only etiketini taşır.
  ENTRY reduce-only olamaz; koruyucu roller reduce-only olmak zorundadır.
- En fazla **iki aktif slot** bulunur; üçüncü eşzamanlı slot
  `DENY / THIRD_ACTIVE_SLOT` olur. Bu sınır hesap ve scope genelindedir.
- Aynı hesap/sembol/yön pozisyonu başka scope adıyla ikinci slota bölünemez.
  `BOTH`, aynı hesap/sembolde `LONG` veya `SHORT` ile çakışır. Ayrı LONG/SHORT
  slotları brüt sayılır; risk, marjin ve miktarlar birbirinden düşülmez.
- Aynı slot isteği yeniden gelirse `DUPLICATE_SLOT_NO_RETRY`; değiştirilmiş
  istek gelirse `SLOT_REPLAY_CONFLICT`. Aynı koruyucu rolün ikinci intent'i
  de aynı/değişik kimlikle reddedilir; rezervasyon sayısı ve bütçe artmaz.
- Client id hesap genelinde tekildir. Order id `(account, order_kind)`
  içinde tekildir. NORMAL ve ALGO aynı order id metnine sahip olabilir;
  gözlemde ad alanı dahil tam intent eşleşmesi zorunludur. Ayrı hesapların
  aynı id metinleri birbirinin gözlem veya bütçesini değiştirmez.
- Modelde emekli olan slot/client/order kayıtları silinmez. Tarihsel kimlik
  başka slotla yeniden kullanılamaz. Aktif iki slottan biri kesin mock
  kapanışla emekli olursa yeni kimlikle üçüncü tarihsel slot kurulabilir;
  aktif sayı yine en fazla ikidir.

### Mock risk, marjin ve fon hesabı

- Pozitif, sonlu, sınırlı düz ondalık metin kullanılır; float, bool, NaN,
  Infinity, negatif değer veya bilimsel gösterim kabul edilmez.
- Mock nominal değer `quantity * price`; marjin nominal/kaldıraçtır.
  Marjin 8 ondalık haneye yukarı yuvarlanır. Hesaplama, çağıranın Decimal
  precision/trap ayarlarından ayrı 64 hanelik yerel bağlamda yapılır.
- Her slotun risk/marjin/kaldıraç tavanı ve global risk/marjin/kaldıraç tavanı
  uygulanır. Global kaldıraç tavanı her slot kaldıraç değerini de sınırlar.
  Aktif slot risk ve marjinleri brüt toplanır; zıt yön netleştirmesi yoktur.
- Hesap fonları yalnız kendi hesabının bütün scope'larında paylaşılır.
  Bilinmeyen hesap reddedilir; başka hesabın fonları ödünç alınmaz.
- Slot, kimlik ve bütçe kontrolleri ile ENTRY intent eklenmesi aynı bellek
  mutex'i altındadır. Fon yetersizliği yarım slot/intent veya risk borcu bırakmaz.
- Kısmi ENTRY/STOP/TP dolumu, UNKNOWN, MISSING veya restart, rezervasyonun
  tamamını korur. Kısmi dolumdan sonra CANCELED entry sıfır pozisyon sayılmaz.

`mock_retire_slot` yalnız güncel lease ile; bütün kayıtlı NORMAL/ALGO intent'ler
güncel, kesin ve terminalken, ayrıca bilinen entry dolumu ile koruyucu toplam
dolum eşitken model bütçesini serbest bırakabilir. Bekleyen ALGO STOP, flat
görünümde bile bunu engeller. Bu API emir iptal etmez, owner kilidi açmaz ve
gerçek fon serbest bırakmaz. UNKNOWN/MISSING geçmişi kalıcı inceleme sebebidir.

### Lease, eşzamanlılık ve restart

- `mock_issue_lease`, tam slot bağı/owner/epoch/generation/expiry taşıyan
  mock nesne üretir. Aynı slotta yeni nesil önceki token'ı çitler. Kopya,
  başka store token'ı, başka slot token'ı veya eski nesil kabul edilmez.
- `now_ms < expires_at_ms` zorunludur; tam expiry anı reddedilir. Saat ve TTL
  sınırlı tam sayıdır; gerileyen saat ve taşan son kullanım zamanı reddedilir.
- Lease geçerliliği, iki-slot sınırı, tekil kimlik, bütçe kontrolü ve intent
  kaydı aynı `RLock` altında yapılır. Aralarında dış callback yoktur.
  Buradaki mutex yalnız model içi senkronizasyondur; owner/STOP kilidi değildir.
- Yeni testler gerçek iki Python thread'i ile aynı slota yarışmayı, iki ayrı
  slotun eşzamanlı başarısını, kalan tek slot ve son hesap fonu yarışını
  çalıştırır. Barrier başlangıcı ve üç saniyelik hata sınırı vardır; sleep
  zamanlamasına dayanılmaz. Her yarışta kayıt ve bütçe sonuçları kontrol edilir.
- İki ayrı facade aynı store'a bağlı **simüle süreçlerdir**. Eski neslin
  reddi, yeni neslin rezervasyonu ve ortak restart kapısı bu modelde denenir.
  G43 testleri gerçek ayrı OS süreçleri arasında paylaşılan bellek/kilit kurmaz.
- `mock_restart`, elde kalan model kayıtlarını/bütçeleri korur, epoch'u bir
  kez artırır, lease'leri geçersizleştirir ve bütün istemcileri tek yönlü
  `MANUAL_REVIEW` durumuna geçirir. Saat hatalı olsa da kapı kapanır.
  Yeni slot/lease/koruyucu intent ve bütçe iadesi reddedilir. Sonraki mock
  gözlemler raporu zenginleştirebilir; entry/unlock veya yeniden açılma üretemez.

### Bağımsız gözlemler ve alarmlar

- Gözlem borsa veya imza kanıtı değildir. Yalnız `MockObservation` kabul edilir;
  `signed=True` sözlüğü veya ham payload bir kanıt oluşturmaz ve saklanmaz.
- Her rolün kendi rezervasyon zamanı, sıra numarası, son gözlemi ve son kesin
  dolumu vardır. Tam scope/account/symbol/positionSide/client/order/kind/rol/
  miktar/reduce-only eşleşmesi gerekir. Başka slot gözlemi iki slotu da değiştirmez.
- Gelecek, stale, intent öncesi veya sırası/zamanı gerileyen gözlem reddedilir.
  Aynı gözlem replay'i dolumu tekrar uygulamaz. Azalan dolum, fazla dolum,
  status/miktar çelişkisi ve terminalden açık duruma dönüş reddedilir.
  Koruyucu toplam dolum bilinen entry dolumunu aşarak sahte flat üretemez.
- `UNKNOWN`/`MISSING` yeni dolum kanıtı yaratamaz. Son kesin dolum korunur ve
  belirsizlik kalıcı işaretlenir. Sonradan güncel gözlem gelse bile aynı intent
  yeniden rezerve edilemez. Slotta belirsizlik varken yeni koruyucu rol
  rezervasyonu da kapalıdır; bu bir STOP onarım uygulaması değildir.
- Bilinen dolum veya dolabilecek belirsiz/açık entry varken STOP eksikse
  `STOP_MISSING_DANGER` oluşur. Yalnız intent yeterli değildir. Eski,
  belirsiz, kayıp veya etkisiz STOP ayrı DANGER alarmı üretir. Güncel ALGO
  STOP gözlemi yalnız `MOCK_STOP_OBSERVED` olabilir; gerçek koruma kanıtı yoktur.
- TP1/TP2'nin unknown, stale, partial ve terminal hata alarmları ayrıdır.
  TP başarısı STOP'u var etmez; STOP gözlemi TP unknown alarmını temizlemez.
  Bir slotun belirsizliği diğer slotun gözlemini/lease'ini değiştirmez.
- Rapor her çağrıda sentetik güncelliği tekrar hesaplar; eski STOP bir yeni
  gözlem gelmeden de DANGER olur. Restart raporu MANUAL_REVIEW kalırken STOP
  ve TP alarmları ayrıca görünür. Alarmlar raporda hesaplanır; kalıcı alarm
  kuyruğu veya bildirim gönderimi yoktur.

## Çalıştırma ve test kaydı

Worktree kökünden:

```sh
python3 -B f01_two_slot_manager_offline_g43.py
python3 -B -m unittest discover -s tests -p test_f01_two_slot_manager_offline_g43.py -v
python3 -B -m unittest discover -s tests -v
```

Standalone CLI gerçekten çalıştırıldı: iki mock rezervasyon, üçüncü için DENY
ve `active_slots=2` üretti. JSON çıktısının yetki/unlock alanları doğrulandı.

| Koşu | Test / başarılı | Assertion / hata / atlama | Süre | Çıkış |
| --- | --- | --- | --- | --- |
| İlk hedefli | 79 / 78 | 0 / 1 / 0 | 0.097 sn | 1 |
| Demo testindeki tür hatası düzeltildikten sonra hedefli | 79 / 79 | 0 / 0 / 0 | 0.093 sn | 0 |
| İlk tam paket | 669 / 667 | 0 / 2 / 0 | 14.181 sn | 1 |
| Dört doğrudan izolasyon/yaşam döngüsü testi sonrası son hedefli | **83 / 83** | **0 / 0 / 0** | 0.099 sn | **0** |
| Son tam paket | **673 / 671** | **0 / 2 / 0** | 14.120 sn | **1** |

İlk hedefli koşuda yeni demo testinin liste ile tuple birleştirmesi
`TypeError` verdi; testte tuple listeye çevrilerek düzeltildi. Demo üst
sonucuna da sabit model yetki alanları eklendi. Hata gizlenmedi veya skip
edilmedi. İlk tam koşu sonrasında, iki ayrı slotun eşzamanlı başarısı,
unknown slotun diğer slota etkisizliği, iki slot içinden birinin emekli
edilmesi ve emekli order id geçmişinin korunması için dört test eklendi;
ardından hedefli ve tam paket sıralı tekrarlandı.

İki tam koşuda aynı mevcut hatalar:

1. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
2. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError: No package metadata was found for binance-sdk-derivatives-trading-usds-futures`.

Bu hatalar için bağımlılık kurulmadı, testler değiştirilmedi/atlanmadı.
Yerel kayıtlar:

- `/private/tmp/g43-target-tests-20261110.log`
- `/private/tmp/g43-full-tests-20261110.log`
- `/private/tmp/g43-target-tests-final-20261110.log`
- `/private/tmp/g43-full-tests-final-20261110.log`
- `/private/tmp/g43-demo-20261110.json`

Yeni Python kaynaklarının AST/sözdizimi ve satır sonları kontrol edildi.
Import allowlist testi yalnız standart kütüphaneleri ve dış adaptör
bulunmadığını kontrol eder. Git kontrolünde mevcut izlenen dosya veya staged
değişiklik yoktur; HEAD/dal korunmuştur. Eski modül silme/temizlik yapılmadı.

## Riskler, inceleme ve sonraki güvenli adım

- Model bellek içidir. Gerçek süreç ölümü, kalıcı WAL, fiziksel durability,
  disk replay, gerçek çok süreçli CAS/fence veya owner provenance sağlanmaz.
  Boş yeni store, eski pozisyonun olmadığına kanıt değildir. `mock_restart`
  yalnız elde kalan otoritenin kapalı davranışını modeller. Gerçek recovery
  için dış, kalıcı ve bağımsız doğrulanan otorite ayrıca tasarlanmalıdır.
- Risk, fiyat, kaldıraç ve fonlar sentetik girdilerdir. PnL, ücret, slippage,
  tasfiye, venue marjin kuralları, canlı algo status eşlemesi veya gerçek
  reduce-only davranışı hesaplanmaz. Model tamlığı/canlı kullanım iddiası yoktur.
- Tarihsel kayıtlar ve belirsizlik işaretleri yalnız bellekte, süresiz tutulur.
  Kalıcı alarm teslimi ve otomatik inceleme çözümü yoktur; unlock/reset API'si yoktur.
- Çapraz inceleme bu oturumda yapılmadı; alt ajan veya Claude çağrılmadı.
  Sonraki güvenli adım Claude'un yalnız bu üç dosyayı bağımsız incelemesi ve
  aynı hedefli/tam test komutlarını kendisinin çalıştırmasıdır. **CLAUDE SONRA.**
- Kullanıcının bu offline geliştirme kapsamı için ek onay gerekmiyor.
  Canlı entegrasyon, eski/kritik dosyalarda değişiklik, bağımlılık kurulumu
  veya kalıcı storage tasarımı bu görevin parçası değildir; yapılmadı.
- Geri dönüş: üç yeni standalone dosyayı uygulamaya bağlamamak yeterlidir.
  Mevcut modül, risk limiti, STOP veya canlı kilit üzerinde geri alma gerekmez.
- Dal ve temel commit yukarıdadır; commit/push/merge/PR son aşaması kullanıcı
  talimatıyla Claude'a bırakıldı. Hiçbiri bu oturumda yapılmadı.
