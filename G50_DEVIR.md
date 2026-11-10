# G50 — USUSDT TP olay kanıtı için çevrimdışı korelasyon / Issue #15

Tarih: 10 Kasım 2026. Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g50`.
Dal: `codex/g50-tp-incident-replay`.
Başlangıç ve teslim HEAD/base: `1d9e3aa0131a664b1ddb8f7100ada3f6e7719b5b`.
Yeni commit yok; teslim üç untracked dosyadır. Commit/push/merge/PR yapılmadı.

**Gerçek 6 ve 9 Kasım 2026 USUSDT TP iptallerinin nedeni UNRESOLVED.**
Görevde belirtilen gerçek olay kanıtları repoda mevcut değildir; bu çalışmada
gerçek receipt, stream veya canlı WAL aranmadı/alınmadı. Yeni analizör yalnız
kullanıcının ileride sağlayabileceği, önceden redakte ve normalize edilmiş
çevrimdışı kanıt parçalarını işler. Sentetik test başarısı gerçek olaya kanıt
sayılmaz. G50 hedefli **110/110 başarılı**; tam paket **977 test, 975 başarılı,
0 failure, 2 SDK error, 0 skip** olduğundan tam paket yeşil değildir.
Claude'un bağımsız son kaynak/test incelemesi bekleniyor; yapılmış sayılmadı.

## Teslim ve kapsam

Önce `AGENTS.md`, `G47_BINANCE_RESEARCH.md`, `G48_DEVIR.md` ve geliştirme
`owner_single_execution.py` içindeki `actual_order`, `checked`,
`cleanup_owned_exits` salt okunur incelendi. İlgili çağıranlar ve payload da
kaynak metin olarak okundu. Issue #15 ağdan alınmadı; kullanıcı kapsamı esas
alındı. Başka worktree kullanılmadı, alt ajan/Claude oturumu başlatılmadı.

Yalnız şu yeni depo dosyaları oluşturuldu:

- `tp_incident_evidence_offline_g50.py`: stdlib, strict redakte JSON parser,
  immutable olay/bulgu/rapor tipleri, korelasyon, yerleşik sentetik JSON
  fixture ve stdin/stdout CLI.
- `tests/test_tp_incident_evidence_offline_g50.py`: 110 unittest; sentetik
  JSON fixture'ları Python içinde kurulur, ek veri dosyası oluşturulmaz.
- `G50_DEVIR.md`: bu teslim, şema, kesin test sonuçları ve inceleme sınırları.

Mevcut kaynaklar/testler, owner, G47/G48 ve `DEVIR_NOTU.md` değiştirilmedi.
Genel AGENTS devir/commit maddesi yerine bu görevin açıkça istediği yalnız
`G50_DEVIR.md` ve commitsiz teslim uygulandı. Kullanılmayan eski kod silinmedi.
Geri dönüş için mevcut bir uygulama değişikliği yoktur; yeni modül hiçbir
yürütücüye veya güvenlik kapısına bağlanmaz.

Canlı FAST log/WAL, yasak canlı klasörler, SDK/private API, kimlik bilgileri,
Keychain, ağ/REST/WS, servis/owner/STOP/emir müdahalesi ve paket kurulumu yok.
Yeni modül/test owner veya SDK import etmez. Tam paketteki mevcut SDK kontrat
testi eksik paketi import etmeye çalışırken error verdi; SDK instance veya
gerçek istek oluşmadı. Test çıktıları yalnız geçici `/private/tmp/g50-*.log`
dosyalarına alındı; gerçek olay verisi içermez ve canlı dizine kopyalanmadı.

## Kaynak çağrı akışı ve çıkarım sınırı

Referanslar yalnız yukarıdaki base'in geliştirme kaynağına aittir:

| Kaynak | Okunan davranış | Kanıtlamadığı |
| --- | --- | --- |
| `owner_single_execution.py:564` / `actual_order` | STOP `query_algo_order(clientAlgoId)`; diğer roller `query_order(symbol, newClientOrderId)` | Olay anında bu kodun devrede olduğu veya gerçek sorgu sonucu |
| `owner_single_execution.py:571` / `checked` | `actual_order` sonrası exact receipt alanları; TP kabulü NEW/PARTIALLY_FILLED/FILLED | CANCELED TP için başarılı receipt veya iptal nedeni |
| `one_write` → `checked` → `actual_order` | `run_once` TP1/TP2 normal emir yolunu kullanır; STOP algo yoludur | Gerçek POST veya başarılı emir |
| `owner_single_execution.py:751` / `cleanup_owned_exits` | snapshot/exposure ile FLAT kontrolü; yalnız exact aktif STOP/TP kimlikleri | FLAT'ın her TP için CANCELED olduğu |
| cleanup → `journal(CANCEL_STOP/TP)` → `delete_once` → snapshot | WAL iptal niyeti DELETE çağrısından önce yazılır | WAL satırının başarılı DELETE yanıtı veya belirli aktör/neden olduğu |

Yeni modül statik `SOURCE_REFERENCE` taşır. Testler işlev satırlarını ve
çağrı bağlantılarını AST ile bağımsız olarak kontrol eder; owner'ı import
etmez veya kod parçalarını çalıştırmaz. Kaynak akışından 6/9 Kasım için
cleanup çalıştı, borsa iptal etti ya da kullanıcı yaptı sonucu çıkarılmaz.

## Girdi sözleşmesi ve kullanım

Python 3.10+ sözdizimi, yalnız stdlib; doğrulanan ortam Python **3.13.15**.
Parser API: `parse_document(bytes) -> tuple[Event, ...]`.
Analiz API: `analyze_document(bytes) -> Report`; `Report.to_dict()/to_json()`.
`synthetic_document() -> bytes` iki ayrı gün için açıkça SYNTHETIC JSON verir.

```sh
python3 -B tp_incident_evidence_offline_g50.py --demo
python3 -B tp_incident_evidence_offline_g50.py --help
```

Argümansız CLI yalnız stdin'den G50 JSON okur. CLI dosya yolu/URL kabul etmez,
dosya açmaz ve kendi logunu yazmaz. Çıktı stdout JSON raporudur. Geçerli analiz
exit 0 döndürür; bu yalnız işlemenin bittiğidir, onay veya neden çözümü değildir.
Geçersiz girdi exit 2 ve stderr'de sabit hata kodu/REVIEW_REQUIRED/sıfır yetki
üretir; ham girdi, yol, serbest exception metni veya traceback basılmaz.

Kök nesne **tam olarak** `schema`, `redacted`, `events` alanlarını taşır:

```json
{"schema":"ATLAS_G50_TP_INCIDENT_EVIDENCE_V1","redacted":true,"events":[]}
```

Üst sınırlar 1.048.576 bayt UTF-8 ve 2000 olaydır; hangisi önce dolarsa.
Eksik/fazla alan, nested duplicate key, NaN/Infinity, BOM, geçersiz UTF-8,
bilinmeyen enum ve bool→int dönüşümü reddedilir. Kanonik JSON byte sırası
zorunlu değildir. Her olayda aşağıdaki alanların tümü vardır; uygulanmayan
alanlar null olmalıdır. Tam örnek yerleşik fixture'da bulunur.

| Alan | Sözleşme |
| --- | --- |
| `event_ref`, `incident_day`, `symbol` | Redakte olay alias'ı; yalnız `2026-11-06` veya `2026-11-09`; yalnız `USUSDT` |
| `occurred_at`, `observed_at` | Açık timezone taşıyan RFC3339 altkümesi; saniye zorunlu, 0–6 kesir hanesi; UTC'ye çevrilir |
| `scope` | Tam olarak `account`, `parent`, `slot` alias'ları; 0..2³¹−1 tam int `generation`; `position_side` BOTH/LONG/SHORT |
| `identity` | POSITION/STREAM_GAP için null; diğerlerinde aşağıdaki exact order identity |
| `provenance` | Tam olarak `origin`, `source`, `artifact_ref`, `authenticity`, `coverage` |
| `event_type` | ORDER_STATE / QUERY_MISSING / OPEN_LIST_ABSENT / POSITION / CANCEL_INTENT / DELETE_ACK / STREAM_GAP |
| `status_field`, `status` | Yalnız durum/DELETE_ACK olayında; NORMAL `status`, ALGO `algoStatus`; enum'lar ayrıdır |
| `query_code` | Yalnız QUERY_MISSING için HTTP_404 veya ORDER_NOT_FOUND; diğerlerinde null |
| `orig_qty`, `executed_qty`, `position_qty` | String decimal veya null; float/exponent/NaN/başında sıfır yok; en çok 18 tam + 18 kesir hanesi; negatif yalnız pozisyon |
| `actor`, `reason` | Aktör UNKNOWN/OWNER_REPORTED/USER_REPORTED/EXCHANGE_REPORTED/OTHER_REPORTED; neden null veya REDUCE_ONLY_REPORTED/STP_REPORTED/USER_REPORTED/OTHER_REPORTED; serbest metin yok |
| `sequence`, `sequence_domain` | Birlikte null veya 0..2⁶³−1 tam int + redakte domain; signed snapshot'ta kullanılamaz |

`identity` alanları tam olarak `role`, `kind`, `endpoint`, `client_id`,
`order_id`, `client_algo_id`, `algo_id`, `actual_order_id`'dir. ENTRY/TP1/TP2
yalnız NORMAL ve `/fapi/v1/order` kullanır; client_id zorunlu, order_id nullable,
algo alanları null. STOP yalnız ALGO ve `/fapi/v1/algoOrder` kullanır;
client_algo_id zorunlu, algo_id nullable, normal alanlar null. actual_order_id
yalnız algo_id varken taşınan pasif çocuk referansıdır; normal kimliğe bağ
kurmaz. Bu şema owner-v1 rol/kind ayrımını modeller; Hedge veya account mode
uygunluğu doğrulamaz. position_side yalnız beyan kapsamıdır.

`provenance.origin` SYNTHETIC veya USER_REDACTED; `source` SIGNED_RECEIPT,
USER_STREAM veya WAL'dır. `artifact_ref` redakte alias'tır. `authenticity`
yalnız NOT_VERIFIED_OFFLINE kabul eder. `coverage` FRAGMENT veya
CALLER_CLAIMED_COMPLETE olabilir; ikinci değer de tamlık kanıtı sayılmaz.
WAL, ORDER_STATE/DELETE_ACK kaynağı olamaz; CANCEL_INTENT yalnız WAL,
DELETE_ACK yalnız SIGNED_RECEIPT ve CANCELED, sorgu yokluğu yalnız
SIGNED_RECEIPT, STREAM_GAP yalnız USER_STREAM kabul eder.

Timestamp'lerde naive zaman, epoch sayısı, `-00:00` belirsiz offset'i, leap
second, geçersiz gün ve ±14:00 dışı offset reddedilir. UTC günü gece yarısında
değişebilir; incident_day zaman damgasından tahmin edilmez, kullanıcı açıkça
etiketler. `observed_at < occurred_at` saat çelişkisi olarak incelemeye düşer.
Duvar saati doğruluğu/freshness veya farklı kaynak saatlerinin senkron olduğu
iddia edilmez.

## Gizlilik, korelasyon ve bulgular

Kullanıcı ham kayıtları **girdi vermeden önce** bu şemaya çevirmeli; account,
scope, slot, olay/artifact/sequence domain, normal/algo client/server/çocuk
kimliklerini tutarlı `r:[a-z][a-z0-9_-]{0,31}` alias'larıyla değiştirmelidir.
Ham imza, anahtar, cookie, token, URL/query string veya serbest log satırı
şemaya dahil değildir. `redacted=true` ve alias biçimi redaksiyonun gerçekten
doğru olduğunu kanıtlamaz; içerik taraması ya da secret okuyucu yoktur.

Çıktı, bu alias'ları da deterministik account/scope/slot/lifecycle/client/
server/artifact/event takma adlarına çevirir; ham alias veya bunların düz
hash'leri rapora taşınmaz. Role yalnız kapalı ENTRY/TP1/TP2/STOP enum'udur;
kimlik içeren serbest role adı kabul edilmez. Zaman ve miktar beyanları
raporda korunur; bu araç genel anonimleştirme aracı değildir. Dahili typed
`Event` nesneleri redakte alias taşır; debug/repr ham verisi loglanmaz.

- Korelasyon namespace'i gün + köken + hesap + parent/slot/generation/
  position_side + sembol + role/kind/client'tır. İki gün aynı USUSDT ID ve
  event_ref değerlerini tekrar kullansa da bağ kurulmaz. SYNTHETIC kayıt,
  USER_REDACTED kaydın eksik makbuzunu veya DELETE yanıtını tamamlayamaz.
- NORMAL ve ALGO client/server alanları ayrıdır. Server kimliği başlangıçta
  null olup aynı client lifecycle'ına sonradan eklenebilir. Farklı server
  kimliği çelişkisi veya scope/slot/role yeniden kullanımı REVIEW_REQUIRED
  olur. Server çelişkisinde intent/yanıt bağlantısı kurulmaz.
- Olay zamanı, gözlem zamanı ve sabit içerik tie-breaker'ıyla deterministik
  timeline üretilir. Tam normalize tekrar tek satır + DUPLICATE_EVENT olur;
  aynı gün/kökende aynı event_ref farklı içerikteyse ikisi korunur ve çelişki
  raporlanır. Eşdeğer timezone normalize edilir; decimal metni ayrıca aynıdır.
- Eşzamanlı durum/miktar çelişkisi, terminal sonrası farklı durum, kümülatif
  fill gerilemesi ve değişen orig_qty incelenir. Kaynaklar arasında birini
  doğru seçen otorite sırası yoktur. Büyük çelişki grubunda her olay çifti
  için bulgu üretilmez; bulgu sayısı karesel büyümez.
- Sequence domain açık verilmelidir. İlk sıra numarası tahmin edilmez.
  Atlamalar yalnız sağlanan parçanın süreksizliğidir; filtreleme/eksik export
  ile gerçek exchange stream kaybı ayrıştırılamaz. Tekrar sıra numarası,
  bildirilen stream gap ve saat çelişkisi REVIEW_REQUIRED üretir.
- 404/order-not-found ve açık listede yokluk CANCELED/FILLED değildir.
  EXPIRED/EXPIRED_IN_MATCH belirli exchange nedeni veya STP kanıtı değildir.
  Signed etiketli FLAT dahi her TP'nin iptalini kanıtlamaz.
- CANCEL_INTENT başarılı DELETE değildir. Sonraki CANCELED yalnız zaman
  ilişkisi sağlar. Eşleşen DELETE_ACK yalnız yanıt **beyanıdır**; istek
  yürütümü, imza, aktör ve gerçek neden bu araçla doğrulanmaz. Başka
  scope/slot/gün/kökendeki ACK eşleştirilmez.
- Kısmi fill, fill/cancel geçişi ve miktar çelişkileri ayrı raporlanır;
  TP1'in kısmi dolması TP2'nin neden iptal olduğunu veya kalan pozisyonu
  belirlemez. Aktör bilinmiyorsa ACTOR_UNKNOWN; açık aktör/neden etiketi
  varsa CLAIM_UNVERIFIED üretilir. H1–H6 hipotezlerinden biri seçilmez.

`FindingCode` ve `Verdict` enum'ları ile frozen bulgular/rapor kullanılır.
İzin verilen verdict yalnız UNRESOLVED/REVIEW_REQUIRED'dır. Eksik gerçek
kaynaklar, eksik TP1/TP2 durumları ve exact lifecycle için signed durum
beyanının yokluğu ayrıca yazılır. Tüm parçalar sağlansa bile
`real_incident_cause=UNRESOLVED`; entry_authorized, owner_authorized, unlock,
stop_proven her zaman false'tur. Rapor hiçbir owner, giriş, unlock veya STOP
proof tüketicisine bağlanmaz. Bellek tipleri kötü niyetli süreç içi Python
koduna karşı güvenlik sınırı değildir.

## Test kanıtı

Runner: `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`,
Python 3.13.15, `-B`. Paket kurulmadı, test atlanmadı/devre dışı bırakılmadı.

```sh
python3 -B -m unittest discover -s tests -p test_tp_incident_evidence_offline_g50.py -v
python3 -B -m unittest discover -s tests -v
```

| Koşu | Toplam | Başarılı | Failure | Error | Skip | Sonuç |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| İlk hedefli | 104 | 101 | 3 | 0 | 0 | FAILED, 1.400 s |
| Gizlilik testi düzeltmesi sonrası | 104 | 104 | 0 | 0 | 0 | OK, 1.260 s |
| Son kaynak ve 6 ek sınır testi | 110 | 110 | 0 | 0 | 0 | OK, 1.736 s, exit 0 |
| Tam paket, bir koşu | 977 | 975 | 0 | 2 | 0 | FAILED, 16.238 s, exit 1 |

İlk statik parse kontrolünde yeni test dosyasındaki iki fazla parantez
düzeltildi; o adım test koşusu değildi. İlk hedefli koşunun üç failure'ı,
gizlilik assertion'ının statik `actual_order:` kaynak referansındaki `r:`
alt dizisini de alias saymasındandı. Kontrol JSON string değerlerindeki
`"r:[a-z]` biçimine düzeltildi; özgün fixture kimliklerinin çıkmaması ve tüm
kimlik alanlarının yeniden adlandırılması denetimleri korunmuştur. Sonra
çıktı sözlüğü mutasyonunun statik referansı değiştirmemesi sağlandı ve altı
ek sınır testi eklendi. Son 110 test tam pakette de geçti. Subtest döngüleri,
24 permutation ve subprocess tekrarları ayrı test sayısına eklenmedi.

Tam paketteki mevcut iki error:

1. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: binance_sdk_derivatives_trading_usds_futures`.
2. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError: binance-sdk-derivatives-trading-usds-futures`.

G40'ın 5 testi ve
`test_protective_real_clock_fence_offline.CrossProcessDeadlineTests.test_no_implicit_retry_when_child_releases_after_timeout`
bu koşuda geçti. Önceki timing/flaky sorununun düzeldiği iddia edilmez.
Başarısız tam paket tekrar çalıştırılarak yeşil gösterilmedi; SDK kurulmadı.

Geçici koşu kayıtları ve SHA-256 (kalıcı saklama garantisi yok):

```text
tp_incident_evidence_offline_g50.py
7fd75a016a91d7e8869990ee8cf3edc8eaf0193d53275a0639b1fdd1f7cb4587
tests/test_tp_incident_evidence_offline_g50.py
0ffe2b2549c16877592285d03ecef1300feb57536fc6b463a82e16e4f79f0504
/private/tmp/g50-targeted-1.log
22f0549df9f1bf6f777571f11e604cce4c179cc294192a0aa4181806948965cb
/private/tmp/g50-targeted-2.log
e780b6a806d91786f6d48048d3d87ce7dfc76c28bc30cbf435b6da3c304799f5
/private/tmp/g50-targeted-3.log
5cd182702c032c1be4617e44eb6b17d2472ac051b06858522ffe342027db3ea2
/private/tmp/g50-full-1.log
944059a2c6122168b31c3a70ef1285221fb4523286fb431a4fefc6f3098e2bba
```

## İnceleme ve sonraki güvenli adım

Mevcut kaynak değişmediği, gerçek kanıt bulunmadığı ve tam paket iki mevcut
SDK error'ı taşıdığı açık tutulur. Claude yalnız bu üç yeni dosyayı son
aşamada bağımsız incelemeli; özellikle kaynak çağrı akışı, normal/algo ve
iki-gün namespace ayrımı, çelişkide bağ kurmama, gizlilik ve sıfır yetki
sınırlarını kontrol edip hedefli/tam testleri kendisi çalıştırmalıdır.
Bu oturum Claude incelemesi/onayı üretmedi.

İleride kullanıcı kendi sağlarsa redakte çevrimdışı kanıtlar bu şemaya ayrı
normalize edilip incelenebilir. G50 canlı veri toplama, owner entegrasyonu,
emir/STOP müdahalesi veya risk kapısı değişikliği yetkisi vermez. Mevcut
çevrimdışı teslim için ek kullanıcı onayı istenmiyor; bu kapsamın dışındaki
her iş yeni görev ve ilgili bağımsız inceleme gerektirir.

**GERÇEK NEDEN UNRESOLVED / İŞLEM YETKİSİ YOK / CLAUDE SON İNCELEMESİ BEKLİYOR.**
