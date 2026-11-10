# G48 — Açık profilli kind uyumluluk adayı / GitHub Issue #13

Tarih: 10 Kasım 2026. Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g48`.
Dal: `codex/g48-tp-kind-compat`.
Başlangıç ve son HEAD/base: `2677e538c6462aedd20df5e82aae9341d8be277e` (`2677e53`).
Yeni commit oluşturulmadı; dosyalar untracked teslimdir.

**Durum:** Bağımsız OFFLINE protokol adayı ve 85 yeni test hazır; hedefli
testler başarılıdır. Tam paket başarılı değildir: 867 test, 865 başarılı,
0 failure, 2 SDK error, 0 skip. Claude'un son bağımsız kaynak/test incelemesi
ve kendi test koşusu bekliyor. Gerçek owner entegrasyonu hâlâ yapılmadı.
Bu teslim işlem izni, owner onayı, unlock veya gerçek STOP proof üretmez.

## Kapsam ve kanıt

Önce `AGENTS.md`, `G47_BINANCE_RESEARCH.md`, `G46_DEVIR.md` ve
`G45_ENTEGRASYON_SOZLESMESI.md` okundu. G46 modülü/testleri ve geliştirme
owner kaynağındaki ilgili alanlar salt okunur incelendi; owner import edilmedi.
Issue #13 ayrıca alınmadı: görev, kullanıcının verdiği kapsam ve yerel
belgelerle, ağ erişimi olmadan yürütüldü.

Yalnız şu üç yeni dosya oluşturuldu:

- `identity_kind_compat_offline_g48.py`: yalnız stdlib; açık profilli immutable
  kimlik, strict parser, canonical serialization/hash, kapalı mock receipt ve
  bellek içi `OfflineCompatConsumer`.
- `tests/test_identity_kind_compat_offline_g48.py`: 85 yeni unittest.
- `G48_DEVIR.md`: bu devir ve açık test sonuçları.

Mevcut kaynaklar/testler, G46 ve `DEVIR_NOTU.md` değiştirilmedi.
`git diff --exit-code -- .` tracked dosyalarda fark göstermedi. Başka worktree,
canlı klasörler, secret/Keychain, Binance API/WS, ağ ve paket kurulumu
kullanılmadı. Commit/push/merge/PR yapılmadı. Mevcut tam paket kendi offline
geçici dosya/süreç fixture'larını çalıştırdı; mevcut SDK testi eksik SDK'yı
import etmeye çalışırken error verdi, gerçek SDK instance/isteği oluşmadı.

G47'nin somut bulgusu role/kind çakışmasıdır: geliştirme owner'ı ENTRY ve
TP1/TP2 için NORMAL `/fapi/v1/order`, STOP için ALGO `/fapi/v1/algoOrder`
kullanır. G46'nın `ENTRY=NORMAL, STOP/TP=ALGO` kuralı korunmuş, bu kuralı
gerçek owner'a otomatik uygulayan bir bağlantı eklenmemiştir. Bu çalışma
USUSDT iptallerinin nedenini belirlemez; G47'deki hipotezler doğrulanmış
nedenlere çevrilmemiştir. Gerçek makbuz ve execution reason kanıtı yoktur.

## Yeni protokol ve profil sözleşmesi

Protokol: `ATLAS_G48_IDENTITY_KIND_COMPAT`; `schema_version` yalnız tam
`int(1)` kabul eder. Bu ayrı G48 şeması, G46 `CanonicalIdentityV1` değildir.

| Açık profil | ENTRY | STOP | TP1 / TP2 | Mode sınırı |
| --- | --- | --- | --- | --- |
| `owner-v1` | NORMAL | ALGO | NORMAL | ONE_WAY / BOTH |
| `conditional-algo-v2` | NORMAL | ALGO | ALGO | ONE_WAY / BOTH veya HEDGE / LONG–SHORT; yalnız hipotetik fixture |

`conditional-algo-v2`, gerçek owner veya Binance tarafından doğrulanmış
bir uygulama sürümü değildir. `owner-v1` gerçek owner'ın ONE_WAY/BOTH
sınırını da korur; Hedge desteği iddia etmez. İki profil de yalnız kimlik
ve eşleştirme sözleşmesidir: order type, fiyat, miktar, reduceOnly,
closePosition, filtreler ve risk intent doğrulaması bu adayda yoktur.

- NORMAL yalnız `/fapi/v1/order`, `client_namespace="newClientOrderId"`,
  dolu `client_id`, null `client_algo_id`, null `algo_id/actual_order_id`
  kullanır. `order_id`, null veya kanonik pozitif ondalık metindir.
- ALGO yalnız `/fapi/v1/algoOrder`, `client_namespace="clientAlgoId"`,
  dolu `client_algo_id`, null `client_id/order_id` kullanır. `algo_id`,
  null veya aynı biçimde metindir. İki client alanının birlikte dolu veya
  boş olması reddedilir; normal readback `clientOrderId` alias'ı çevrilmez.
- `actual_order_id` yalnız `algo_id` varken taşınabilen pasif çocuk
  referansıdır. Normal server identity yerine geçmez; ownership veya
  STOP kanıtı değildir. Aynı rakamların NORMAL ve ALGO namespace'lerinde
  bulunması kimlikleri birleştirmez. Genel `server_order_id` alias'ı yoktur.
- Server ID'ler 1–20 basamaklı, sıfırla başlamayan ASCII pozitif metindir;
  int/bool/float, işaret, whitespace ve sıfır reddedilir. Client metni
  bilerek dar 1–36 karakter ASCII altkümesidir; tüm Binance ID biçimlerini
  desteklediği iddia edilmez. Trim, case conversion veya symbol alias yoktur.
- ONE_WAY yalnız BOTH; HEDGE yalnız LONG/SHORT kabul eder. Hedge LONG
  entry BUY, SHORT entry SELL ister. ENTRY order_side entry_side ile aynı;
  STOP/TP order_side ters yönde olmalıdır. Tüketici hesap mode değişimini
  bütün instance ömründe reddeder; bu exchange mode doğrulaması değildir.

Identity/receipt parser'ları ve tüketici oluşturulması açık, typed
`expected_profile` ister; varsayılan yoktur. Payload profiliyle çelişki
`EXPECTED_PROFILE_CONFLICT` olur; ENTRY eşleştirmesi iki profilde aynı olsa
da reddedilir. Kind/endpoint'ten profil tahmini, fallback, G46 importer'ı,
otomatik upgrade veya sessiz downgrade bulunmaz.

Tam G48 composite key sırası:

```text
protocol / schema_version / profile / environment / venue / account_id /
parent_scope_id / execution_epoch / slot_id / slot_generation / symbol /
position_mode / position_side / entry_side / order_side / order_role /
order_kind / endpoint / order_identity
```

Venue yalnız `BINANCE_USDM`; environment `OFFLINE/TESTNET/MAINNET` pasif
namespace etiketleridir. Son iki değer erişim veya işlem izni vermez.
Profile, endpoint ve bütün iç order alanları serialization/hash kapsamındadır.
Kimlik değişmezdir: sonradan server/child ID eklenmesi zenginleştirme diye
kabul edilmez; aynı role/client binding'ine yeni identity bağlamak reddedilir.

## Canonical wire ve kayıt tüketimi

Wire yalnız en fazla 16384 bayt UTF-8 immutable bytes kabul eder. JSON
anahtarları sıralı, ayraçları kompakt, `ensure_ascii=True`, `allow_nan=False`
olmalıdır. Eksik/fazla alan, nested duplicate field, bilinmeyen enum/version,
bool/int coercion, BOM, alternatif escape, whitespace ve newline reddedilir.
Dict builder alan sırasını normalize eder; wire parser kanonik olmayan byte'ı
sessizce normalize edip kabul etmez. Null/False/hash alanları da zorunludur.

```text
identity_hash = SHA256(b"ATLAS/G48/KIND_COMPAT/IDENTITY/V1\0" + canonical_identity)
receipt_hash  = SHA256(b"ATLAS/G48/KIND_COMPAT/MOCK_RECEIPT/V1\0" + canonical_payload)
```

Receipt payload yalnız receipt_hash alanını dışarıda bırakır; wire hash'i
yeniden hesaplananla eşit olmalıdır. Bu özel G48 formatıdır; genel RFC
canonical JSON uyumluluğu veya kriptografik authenticity iddiası değildir.

Mock receipt `ACKED/OBSERVED/FILLED/CANCELED/MISSING/UNKNOWN/MODEL_RETIRED`
durumlarını taşır. Her durumda `OFFLINE_MOCK`, `FAIL_CLOSED`, `NO_RETRY` ve
`entry_authorized=false`, `owner_authorized=false`, `unlock=false`,
`stop_proven=false` zorunludur. False yerine `0/null/"false"` kabul edilmez.
`bool(receipt)` TypeError üretir. Yeniden hash'lenen olumlu yetki alanı da
reddedilir. `MISSING`, CANCELED veya FILLED olarak yorumlanmaz.

`OfflineCompatConsumer.consume(bytes)` doğrulanan kapalı receipt'i döndürür:

- Event ID instance genelinde tekildir: tam tekrar `DUPLICATE_EVENT`,
  değişmiş içerik `EVENT_ID_COLLISION` olur. Yeni event ID ile eski sequence
  replay'i reddedilir. Sequence aynı account/parent içindeki slotlarca
  paylaşılır; ilk olay parent=null, devam aynı exact identity'nin son olayına
  bağlı olmalıdır. Bu sequence gerçek WAL sequence değildir.
- Execution epoch account/parent watermark'ıdır; slot generation aynı
  parent/slot'ta gerileyemez. Yeni execution epoch, daha önce kullanılan
  slotta daha yüksek generation ister. 20→21→20 ve eski epoch geri dönüşleri
  reddedilir. Aynı nesilde symbol/mode/position_side/entry_side değişemez.
- Client ve server tombstone'ları environment/venue/account/kind kapsamlıdır;
  role, slot, parent veya generation değiştirerek yeniden kullanım yapılamaz.
  NORMAL ve ALGO ayrıdır. Lifecycle+role tek exact identity'ye bağlıdır.
  MODEL_RETIRED bu bağları serbest bırakmaz; retention/silme API'si yoktur.
- Bütün kontroller tamamlanmadan state değişmez. Reddedilen yüksek epoch
  watermark'ı ilerletemez, event ID veya namespace'i zehirleyemez.

## Test kanıtı

Runner: `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`,
Python **3.13.15**, `-B`. Yeni uygulama/test için bağımlılık kurulmadı.

```sh
python3 -B -m unittest discover -s tests -p test_identity_kind_compat_offline_g48.py -v
python3 -B -m unittest discover -s tests -v
```

| Koşu | Toplam | Başarılı | Failure | Error | Skip | Sonuç |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| G48 hedefli | 85 | 85 | 0 | 0 | 0 | 0.112 s, exit 0 |
| Tam paket | 867 | 865 | 0 | 2 | 0 | 11.777 s, exit 1 |

Hedefli ve tam koşu birer kez çalıştırıldı. G48'in 85 testi tam pakette de
geçti; subtest iterasyonları test sayısına eklenmedi. Başarısız koşu yeşile
çevrilmek üzere tekrarlanmadı, hiçbir mevcut test değiştirilmedi/atlanmadı.

Kapsanan başlıca regresyonlar: iki profilin role/kind matrisi; owner TP'nin
ALGO olmasının reddi; expected profile conflict; eksik/bilinmeyen profil ve
sessiz downgrade reddi; endpoint/namespace/order-ID karışıklıkları; ambiguous
clientAlgoId; mode/side/yön çelişkileri; aynı sembolde Hedge LONG/SHORT
izolasyonu ve Hedge/One-way çelişkisi; duplicate/collision; stale generation,
ABA/restart epoch; sequence replay/causal chain; reddin state'i değiştirmemesi;
tam canonical identity/receipt roundtrip ve golden bytes; bütün mock
durumlarında sıfır yetki; yeniden hash'lenen sahte kayıt ve restart sınırları.

Tam koşudaki iki error:

1. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
2. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError: binance-sdk-derivatives-trading-usds-futures`.

G40'ın 5 fixture testi ve G46 devrinde hata verdiği belirtilen
`test_protective_real_clock_fence_offline.CrossProcessDeadlineTests.test_no_implicit_retry_when_child_releases_after_timeout`
bu koşuda geçti. Bu tek geçiş, daha önce belgelenen timing/flaky sorununun
düzeldiğinin kanıtı değildir; ilgili mevcut test veya kaynak değiştirilmedi.

Loglar `/private/tmp/g48-targeted-1.log` ve `/private/tmp/g48-full-1.log`
konumundadır; geçici dosyalardır, kalıcı saklama garantisi yoktur.

SHA-256:

```text
identity_kind_compat_offline_g48.py
e566d7c4147d44b610651797c1accaee721e524c3fe7b9ca3ab1219a6872506d
tests/test_identity_kind_compat_offline_g48.py
2d58f24948a0798dbd680ebd7ee0db5cf3c82ccbb6e835742673904ab0fad5e0
/private/tmp/g48-targeted-1.log
f85f96b32d8c3c071fb66c39c4d9363c221d3f5a7b47a28a415de2ce3f577f2c
/private/tmp/g48-full-1.log
10ac91bc108498a6b31a503005af3d5915935b7dedd0eca1a34a8f44f6a470d1
```

## Açık sınırlar, inceleme ve sonraki güvenli adım

1. **Gerçek owner entegrasyonu hâlâ yapılmadı.** G46 düzeltilmiş sayılmaz;
   legacy/G46 dönüşüm adaptörü ve G37–G46/owner import bağlantısı yoktur.
   Owner kilidi, STOP/guard, risk ve yürütme davranışı değiştirilmedi.
2. Sonuç yalnız G45 P0-01/P0-06 için T0 aday kanıtıdır. Hiçbir P0 satırı
   bütünüyle kapatılmadı; gerçek fence, WAL/fsync, ledger, signed readback,
   STOP coverage ve owner onayı kanıtı üretilmedi. Tam paket yeşil değildir.
3. Generation/epoch/sequence değerleri caller kontrollü sentetiktir;
   authoritative generation veya saat güncelliği yoktur. Tüketici tek
   işbirlikçi akış/bellek fixture'ıdır; thread/process atomikliği ve kalıcı
   restart koruması yoktur. Yeni instance aynı makbuzu tekrar kabul edebilir;
   bunun yine sıfır yetkili kaldığı sınır testi vardır. Tombstone belleği büyür.
4. Hash imza/HMAC değildir. Biçimsel olarak geçerli, yeniden hash'lenen
   sahte ilk kayıt kabul edilebilir; bunun da sıfır yetkili kaldığı testlidir.
   Frozen tipler süreç içi saldırgan Python koduna karşı koruma değildir.
5. Client ID altkümesi ve immutable server/child ID kuralı gelecekte ayrı
   entegrasyon tasarımı ister. Tam emir payload'ı, miktar/partial fill/TP2
   kalan miktarı ve borsa durum semantiği bu kimlik modelinden kanıtlanamaz.
6. Claude incelemesi yapılmadı ve onay imzası varsayılmadı. Alt ajan veya
   başka worktree kullanılmadı. Son bağımsız inceleme Claude'a bırakıldı.

Sonraki güvenli adım: Claude'un bu üç dosyayı bağımsız incelemesi, özellikle
owner-v1 TP NORMAL eşleşmesini, expected-profile reddini, namespace/side
kurallarını ve sıfır yetki sınırını değerlendirmesi; hedefli testleri kendisinin
çalıştırması ve tam paket SDK hatalarını açık tutması. Bu OFFLINE teslim için
ek onay istenmiyor. Gerçek owner/STOP/guard entegrasyonu bu görevin dışındadır;
ileride ayrı kullanıcı onayı ve bağımsız inceleme olmadan başlatılamaz.

**CANLI YETKİ YOK / YENİ GİRİŞ YOK / UNLOCK YOK / ÜRETİM YAMASI YOK.**
