# G46 — V1 canonical identity ve kapalı typed decision / Issue #11

Tarih: 10 Kasım 2026. Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g46`.
Dal: `codex/g46-identity-decision`.
Başlangıç ve son HEAD: `a54775e7662b65e7161fcddbde7fdc06ce55d36f`.
Bu kimlik temel commit'tir; yeni değişiklik commit'i oluşturulmadı.

**Durum:** Yeni OFFLINE uygulama ve 109 hedefli test hazır. Tam test paketi
başarısız: 782 test, 779 başarılı, 1 assertion failure, 2 error, 0 skip.
Claude'un son bağımsız incelemesi bekliyor. G45 P0-01/P0-06 için yalnız T0
fixture/tüketici kanıtı üretildi; P0 kabul satırlarının bütünü kapatılmadı.
Üretim, yeni giriş, unlock veya gerçek STOP doğrulama onayı değildir.

## Teslim ve korunmuş kapsam

Yalnız şu üç yeni dosya oluşturuldu:

- `identity_decision_contract_offline_g46.py`: stdlib tabanlı typed V1 şema,
  strict parser, immutable identity, canonical serialization/receipt hash ve
  çalışan bellek içi `OfflineDecisionConsumer`.
- `tests/test_identity_decision_contract_offline_g46.py`: 109 yeni unittest.
- `G46_DEVIR.md`: bu devir, test sonuçları ve kanıt sınırları.

Önce `AGENTS.md`, `G45_ENTEGRASYON_SOZLESMESI.md` ve
`G44_A_ENTEGRASYON_DENETIM.md` okundu. Üretim ve tüm mevcut G37–G45
dosyaları/testleri değiştirilmedi. `git diff --exit-code -- .` başlangıçtaki
tracked dosyalarda fark göstermedi. Kullanıcının bu göreve özgü talimatıyla
`DEVIR_NOTU.md` değiştirilmedi, commit/push/merge/PR yapılmadı. Ağ/GitHub
erişimi olmadı; Issue #11'in içeriği ayrıca alınmadı.

Başka worktree, canlı dizinler, Keychain/secret/credential, gerçek Binance,
owner SDK ve servis kullanılmadı. Gerçek emir/STOP/kilit/risk politikası
değişikliği yapılmadı. Gerçek importer, legacy sonuç adapter'ı veya mevcut
modüllere import bağlantısı eklenmedi. Paket kurulmadı. Tam paket mevcut
OFFLINE testlerin kendi geçici dosya/süreç fixture'larını çalıştırdı; eski SDK
sınıfı testi import denemesinde eksik modül hatası verdi.

## V1 sözleşmesi

`CanonicalIdentityV1.composite_key` şu 13 bileşeni bu sırayla taşır:

```text
environment / venue / account_id / parent_scope_id / execution_epoch /
slot_id / slot_generation / symbol / position_mode / position_side /
order_role / order_kind / order_identity
```

- `schema_version` tam `int(1)` olmalıdır; bool/float/string dönüşümü yoktur.
  Epoch/generation pozitif ve en fazla `2**63-1`; monotonic ns sıfır olabilir.
  Alan eksik/fazla/tekrarlı, bilinmeyen enum/version, boş isim veya alias ret.
- Ortamlar `OFFLINE/TESTNET/MAINNET`; son ikisi yalnız pasif namespace
  etiketidir. Venue/symbol büyük harfli ASCII; referanslar case-sensitive ASCII;
  slot yalnız `A/B`. Trim, harf dönüşümü veya symbol alias çözümlemesi yoktur.
- Bu dar V1'de `ENTRY→NORMAL`, `STOP/TP1/TP2→ALGO` zorunludur. Bu seçim
  borsanın tüm emir çeşitleri hakkında iddia veya legacy uyumluluk değildir.
  NORMAL yalnız `client_id`, ALGO yalnız `client_algo_id` kullanır. Diğer
  alan zorunlu `null` olur. `server_order_id` zorunlu alan olup `null` veya
  1–20 haneli, sıfırla başlamayan pozitif ondalık metindir.
- `ONE_WAY→BOTH`, `HEDGE→LONG/SHORT`. Tüketici aynı hesapta mode değişimini
  instance ömrü boyunca reddeder; bu gerçek exchange mode doğrulaması değildir.
  Aynı sembolde hedge LONG/SHORT ayrı slot ve ayrı kimliklerle taşınabilir.
- Identity, iç order identity, lease ve envelope frozen/slots dataclass'tır.
  Dict girişleri kopyalanıp immutable değerlere ayrıştırılır; dış dict veya
  export mutasyonu kaydı değiştirmez. Server id sonradan eklenemez; bu da
  kimlik değişimidir ve client binding çakışmasıyla reddedilir.
- `LeaseV1(lease_epoch, lease_generation, lease_owner)` ve
  `risk_reservation_id` identity key'den ayrıdır; envelope receipt'ine dahildir.
  Risk id yalnız referanstır, gerçek rezervasyon yapmaz; intent/policy hash'leri
  de opaque içerik referansıdır, intent/policy doğruluğunu kanıtlamaz.
- Envelope ayrıca event/parent event, açık clock source/monotonic ns, biçimi
  doğrulanan UTC gözlem zamanı, WAL sequence, evidence/status/provenance ve
  durability sınıfı taşır. UTC/clock alanları gerçek tazelik kanıtı sayılmaz.

Her `DecisionEnvelopeV1` yalnız `FAIL_CLOSED`, `entry_authorized=false`,
`unlock=false`, `stop_proven=false`, `NO_RETRY` olabilir. Alanların `0`,
`"false"`, `"NO_UNLOCK"`, `null` gibi taklitleri de reddedilir. `bool(envelope)`
TypeError üretir. Desteklenen bütün 14 model durumu tüketildikten sonra da
kapalıdır. STOP rolünde mock FILLED dahi gerçek STOP proof değildir.
`signed/owner_approved/stop_present` gibi fazladan boolean'lar kabul edilmez.

`ACKED` yalnız `CALLBACK_ACK/OFFLINE_CALLBACK/CALLBACK_ACK_ONLY`;
`DURABLE` yalnız `LOCAL_JOURNAL/OFFLINE_LOCAL_JOURNAL/LOCAL_FSYNC_ACK_ONLY`
etiketleriyle taşınır. `RESERVED/MODEL_RETIRED/MANUAL_OWNER_REVIEW` model
sonucudur. Kaynak veya durability sınıfları birbirine dönüştürülemez; hiçbir
etiket gerçek borsa kabulü, owner grant veya STOP yetkisi üretmez.

## Serialization ve tüketici davranışı

Wire yalnız en fazla 32768 bayt immutable UTF-8 `bytes` kabul eder. JSON
anahtarları sıralı, ayraçlar kompakt, `ensure_ascii=True`, NaN/Infinity yasaktır.
Parser duplicate key'leri iç nesneler dahil reddeder; parse sonrası kanonik
byte eşitliği whitespace, key order, alternatif escape/numeric biçimleri,
BOM ve sondaki newline'ı reddeder. Dict builder alan sırasını normalize eder;
wire parser noncanonical girdiyi sessizce normalize ederek kabul etmez.

```text
identity_hash = SHA256(b"ATLAS/G46/IDENTITY/V1\0" + canonical_identity_bytes)
receipt_hash  = SHA256(b"ATLAS/G46/DECISION/V1\0" + canonical_envelope_payload)
```

Payload tüm envelope alanlarını kapsar; yalnız `receipt_hash` hariçtir.
Wire hash alanı zorunludur ve yeniden hesaplanan hash ile aynı olmalıdır.
Bu format G46 V1'e özgüdür; genel RFC canonical JSON uyumluluğu iddiası yoktur.

`OfflineDecisionConsumer.consume(bytes)` parse/doğrulama sonrası typed kapalı
envelope döndürür; ret `ContractError` ile yapılır. Ham girdi hata mesajına
eklenmez. Bütün kontroller geçmeden fixture state'i güncellenmez.

- Execution epoch watermark'ı environment/venue/account/parent kapsamındadır.
  Slot generation aynı parent+slot için gerileyemez; yeni execution epoch aynı
  slotta daha yüksek generation ister. Aynı generation'da symbol/mode/side
  değişmez. 20→21→20 ve epoch geri dönüşleri reddedilir.
- Lease `(epoch,generation)` ayrı parent watermark'ıdır; leksikografik gerileme
  ve aynı token'da owner değişimi ret. Değerler caller tarafından sağlanır;
  kalıcı veya otoritatif token oldukları doğrulanmaz.
- Client/server namespace'i environment/venue/account/order_kind temelindedir.
  Aynı namespace içinde text'in başka role/slot/generation/parent'a bağlanması
  reddedilir; NORMAL/ALGO ve hesap/venue/environment ayrımları korunur.
  Her lifecycle+role tek identity'ye bağlıdır; tombstone silme API'si yoktur.
- Risk id environment/venue/account kapsamında exact identity+intent+policy'ye
  bağlıdır. Sonraki olayda risk id/intent/policy değişimi ret; model retirement
  client/server/risk referanslarını serbest bırakmaz.
- Event id consumer genelinde tekildir. Tam tekrar `DUPLICATE_EVENT`, değişmiş
  içerikle aynı event id `EVENT_ID_COLLISION` olur. İlk olay parent=null;
  devam olayı aynı identity'nin son event'ine bağlanır. WAL sequence parent
  genelinde artar; monotonic ns identity içinde aynı source ile kesin artar.
  Yeni event id ile eski sequence de replay olarak reddedilir.

## Test kanıtı

Runner: `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`,
Python **3.13.15**, `-B`; yeni modül ve testler yalnız stdlib kullanır.

```sh
python3 -B -m unittest discover -s tests -p test_identity_decision_contract_offline_g46.py -v
python3 -B -m unittest discover -s tests -v
```

| Koşu | Toplam | Başarılı | Failure | Error | Skip | Sonuç |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Son G46 hedefli | 109 | 109 | 0 | 0 | 0 | 0.308 s, exit 0 |
| Tam paket | 782 | 779 | 1 | 2 | 0 | 12.247 s, exit 1 |

109 yeni test: Identity 23, Serialization 16, Envelope 24, Consumer 43,
OfflineBoundary 3. Subtest iterasyonları ayrıca test sayısına eklenmedi.
G46'nın 109 testi tam koşuda da geçti. Tam çıktı:
`/private/tmp/g46-full-SIBrog` (geçici yerel log, kalıcı teslim garantisi yok).

Tam koşudaki başarısızlıklar:

1. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
2. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError` (`binance-sdk-derivatives-trading-usds-futures`).
3. `test_protective_real_clock_fence_offline.CrossProcessDeadlineTests.test_no_implicit_retry_when_child_releases_after_timeout`:
   `AssertionError: ProtectiveJournalDeadlineExceeded not raised` (mevcut test satır 95).

Üçüncü testin mevcut kaynakta sabit 150 ms child sleep/readiness kullandığı
salt okunur görüldü; aynı testin timing/readiness sınırı `G40_DEVIR.md` içinde
önceden belgelenmiştir. Bu koşuda scheduler sebebi bağımsız ölçülmedi; kesin
kök neden veya düzelme iddiası yoktur. Test yeniden koşularak sonuç yeşile
çevrilmedi, assertion gevşetilmedi. İki SDK hatası dışındaki bu failure da
açık tutulur. Hiçbir mevcut test değiştirilmedi, gizlenmedi veya skip edilmedi.

İlk geliştirme koşusu 107 testte 1 failure + 1 error verdi: yanlış tipli
identity test yardımcısında modüle ulaşmadan kullanılmıştı; derin JSON testinde
Python'a bağlı tek ret kodu gereksiz varsayılmıştı. Yalnız bu yeni fixture'lar
düzeltildi; ret beklentisi korundu, iki ek negatif/izolasyon testiyle son sayı
109 oldu. Bu ilk koşu son başarılı hedefli sonuç yerine sunulmaz.

İncelenen kaynak/test ve tam log SHA-256 değerleri:

```text
identity_decision_contract_offline_g46.py
b6c64a7ddcc6ffdfb3743220473f35874a2956372fcf6fa3d8d725e356edb952
tests/test_identity_decision_contract_offline_g46.py
5da8e37647039d05d57ca38a2cc1c60725d962233e3df3e027f877bb3f2c4d98
/private/tmp/g46-full-SIBrog
fcee99e91366af2ffa6565349e28637523fb6ae0a44b6e39a877a195bc5315df
```

## Açık sınırlar ve son inceleme

1. Tüketici tek instance/tek işbirlikçi çağrı akışıdır. Çoklu thread/süreç,
   kalıcı store, crash recovery, disk/fsync, WAL onarımı ve gerçek mutation
   linearization noktası yoktur. Yeni instance aynı receipt'i kabul eder;
   bunun NO_AUTHORITY olarak kaldığı ayrı sınır testiyle gösterilir.
2. Frozen tipler/hash güven kökü veya süreç içi saldırgan koda karşı koruma
   değildir. Geçerli biçimde yeniden hash'lenen sahte ilk girdi kabul edilebilir;
   bu sınır testi vardır ve sonuç yine kapalıdır. Gerçek imza, hesap aitliği,
   kaynak doğruluğu, risk tutarı/rezervasyonu veya STOP coverage kanıtlanmaz.
3. Lease/epoch/generation ve zaman değerleri sentetiktir. Harici watermark,
   güvenilir clock veya otoritatif mode kontrolü sağlanmadı. Tombstone belleği
   sınırsız büyür; retention/compaction protokolü tasarlanmadı.
4. G37–G45 ve gerçek owner tüketicileri bu modüle bağlanmadı. V1 dar role/kind
   matrisi, immutable server id ve account mode sabitliği ayrıca incelenmelidir.
   P0-02/03/04/05/07/08/09 için uygulama veya kapama kanıtı üretilmedi.
5. Tam paket yeşil değildir; eksik SDK ve mevcut timing failure açıktır.
   Bağımsız Claude kaynak/test incelemesi ve Claude'un kendi test koşusu
   yapılmadı. Alt ajan çağrılmadı; inceleme imzası veya onayı varsayılmaz.

Sonraki güvenli adım: Claude'un yalnız bu üç dosyayı ve G45 P0-01/P0-06
beklentilerini bağımsız incelemesi, hedefli testleri kendisinin çalıştırması;
tam paket başarısızlıklarını ayrı tutması. Canlı/üretim bağlantısı, importer,
kilit/STOP/risk değişimi, kurulum veya ağ işlemi için bu çalışma yetki vermez.
Bu OFFLINE teslim için ek onay istenmiyor. Kullanıcının talimatıyla burada
duruldu; Git veya canlı sonraki adım başlatılmadı.
