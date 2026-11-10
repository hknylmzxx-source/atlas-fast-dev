# G39 — Offline TP, kilit kanıtı ve değişmez STOP simülatörü

- Tarih: 2026-11-10.
- Görev: ATLAS FAST / G39 / `hknylmzxx-source/atlas-fast-dev`, Issue #4.
  Görev tanımı kullanıcıdan alındı; ağ yasağı nedeniyle GitHub okunmadı.
- Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g39`.
- Dal: `codex/g39-safety-chain`.
- Başlangıç ve teslim HEAD: `021894c209dd7a9e24de113edabc2e88be3359ed`.
- G39 commit kimliği: yok. Kullanıcı talimatıyla commit/push/merge/PR yapılmadı.
- Durum: yeni simülatör ve hedefli testler hazır; **tam test paketi başarılı
  değildir**. İki mevcut SDK bağımlılık hatası ve son Claude incelemesi açık.

## Yalnız eklenen üç dosya

1. `offline_safety_chain_g39.py`: çalışan standalone simülatör, karar API'si,
   19 sentetik CLI senaryosu, G37 diski üzerinden restart/replay.
2. `tests/test_offline_safety_chain_g39.py`: 52 bağımsız unittest; yalnız mock,
   geçici dizinler ve kontrollü offline alt süreçler.
3. `G39_DEVIR.md`: sonuçlar, sınırlar ve devir bilgileri.

G37/G38 kaynakları ve testleri, kritik mevcut modüller ve `DEVIR_NOTU.md`
değiştirilmedi. Kullanıcının yalnız yeni G39 dosyaları talimatı nedeniyle
devir bu yeni dosyadadır. Eski kod silinmedi; yeni paket/hook eklenmedi.

## Çalışan davranış

G39 aynı worktree'deki gerçek `tp_lifecycle_offline_g37` ve
`lock_evidence_preflight_offline_g38` modüllerini import eder. Yerlerine sahte
modül geçirilmez; iki importun aynı dizinden geldiği ayrıca denetlenir.
Diğer yerel uygulama, owner veya SDK modülleri G39 tarafından import edilmez.

- G37 iki TP için sentetik niyetleri, kaynak nesnesine bağlı mock okumaları,
  değişmez olayları ve disk üzerindeki yerel alarm outbox'ını işler.
- G38 üç ayrı mock kaynağın pozisyon, normal açık emir ve conditional/algo
  açık emir kanıtını; ledger/WAL/incident bağlarını ve snapshot neslini inceler.
  G39 her raporda G38'i yeniden çağırır; eski olumlu kararı önbelleğe almaz.
  Hazır `Decision` nesnesi veya `signed=True` iddiası kanıt kabul edilmez.
- STOP `MockStopSnapshot` adlı frozen gözlemdir; içindeki bağ da frozen'dır.
  Varlığı ve scope/account/symbol bağı kontrol edilir. Yokluğu kapalı sonuç
  üretir; STOP yaratma, emir gönderme, iptal veya onarım yolu bulunmaz.
- TP scope/symbol bağı, preflight ve STOP bağı aynı G39 bağıyla karşılaştırılır.
  G37'nin hesabı temsil eden ayrı bir alanı yoktur; gerçek hesap bağlantısı
  veya hesap provenance'ı bu eşleşmeden çıkarılamaz.

Her raporda kurucudan değiştirilemeyen alanlar:

```text
entry_authorized = false
unlock = false
entry_policy = NO_NEW_ENTRY
post_policy = NO_RETRY
stop_policy = IMMUTABLE_MOCK_ONLY
manual_owner_review_required = true
real_signature_verified = false
live_binance_validated = false
```

Her iki TP `FILLED`, STOP mevcut, kanıtlar uyumlu ve alarm geçmişi yoksa sonuç
yalnız `MANUAL_OWNER_REVIEW` olur. Diğer durumlar `FAIL_CLOSED` üretir.
G37 alarm geçmişi ve aynı G39 örneğinde raporlanan yerel alarmlar kendiliğinden
silinmez. Yeni mock kanıt gelmesi geçmiş alarmı temizlemez. TP karantinası
diğer TP'nin telemetri takibini durdurmaz; STOP her iki TP'den bağımsız kalır.

## Senaryolar ve doğrulanan sonuçlar

| Senaryo | Sonuç / gözlenen davranış |
| --- | --- |
| `normal`, `owner-flat` | İki TP FILLED; yalnız manuel inceleme; izinler kapalı |
| `tp1-expired` | TP1 NEW → EXPIRED; OWNER_ALARM; TP2 izlenebilir |
| `tp2-partial` | TP2 NEW → PARTIAL; miktar korunur; RECONCILE |
| `post-unknown` | UNKNOWN_NO_RETRY; aynı TP için ikinci niyet oluşturulmaz |
| `stop-absent` | STOP_ABSENT_NO_REPAIR; sahte STOP yok kalır |
| `owner-open` | Brüt pozisyon sıfır dışı; kapalı sonuç |
| `preflight-stale` | Eski kanıt, TP dolumlarından bağımsız reddedilir |
| `preflight-forged` | Alanları aynı kopya makbuzun nesne kimliği reddedilir |
| `preflight-mismatched` | Owner hesap bağı uyuşmazlığı reddedilir |
| `conditional-open-order`, `normal-open-order` | Pozisyon sıfır olsa da açık emir kapalı sonuç verir |
| `duplicate`, `reordered` | TP1 karantinaya alınır; dolum geriletilmez; TP2 bağımsızdır |
| `crash-restart` | Gerçek G37 diski replay edilir; UNKNOWN/alarm/niyet sayısı korunur |
| `journal-fsync-unknown` | Dosya fsync belirsizliği kalıcıdır; pending temizlenmez; restart kapalıdır |
| `journal-published-fsync-unknown` | Yayın sonrası son dizin fsync hatası; okunabilir olay tekrar izni vermez |
| `owner-journal-partial`, `owner-journal-toctou` | Yarım durum/eski snapshot reddedilir; temizlik yapılmaz |

Ek unittest kapsamı: owner scope/symbol uyuşmazlığı, gelecekteki zaman,
koşul emri kanıtının eksikliği, bool taklidi, eksik kanıt, kopya ve yabancı
kaynak, çapraz kayıt bağlantısı, bozuk G37 günlüğü, eski yazıcının çitlenmesi,
rapor/STOP değişmezliği, hata metninin sızmaması ve gerçek G38 çağrıları.
Ayrı bir alt süreçte G37 kaydı fsync sonrası `os._exit(17)` ile aniden
sonlandırılır; yeni süreç aynı diskten UNKNOWN ve aynı alarm kimliklerini
okur, yeni niyet oluşturamaz. Bu bir sentetik süreç çökmesidir; güç kesintisi
veya gerçek disk dayanıklılığı testi değildir.

## Günlük ve alarm sınırları

G37 alarmları `JOURNALED_LOCAL_OUTBOX`; G39 karar/hata alarmları yalnız
`MEMORY_ONLY` olarak etiketlenir. OWNER_ALARM / RECONCILE / OWNER_REVIEW türü,
neden kodu ve G37 olay kimliği raporda korunur. Harici owner servisine alarm
teslim edildiği iddia edilmez. G39 belleği restart sonrası kalıcı değildir;
restart daima ayrıca kapalıdır.

Fsync hatası aynı örnekte `UNKNOWN_NO_RETRY` durumunu kalıcı yapar; takip eden
TP yazımları denenmez. Yayın sonrası fsync hatasında olay diskte okunabilse
bile yeni örnek `REPLAY_ONLY_NO_DURABILITY_PROOF` bildirir ve kapalı kalır.
Günlük bilinmiyorsa `synthetic_post_intents=null` olur; okunamayan kayıtlar
"sıfır niyet" veya "TP yok" sayılmaz. Varsa TP görünümleri yalnız son kabul
edilmiş telemetridir. Otomatik onarım, pending temizleme veya retry yoktur.

CLI fsync hata fixture'ı standart kütüphane `unittest.mock.patch` ile G37'nin
fsync çağrısını kısa süreli değiştirir ve geri yükler. Yalnız tek iş parçacıklı,
geçici dizinli offline demo içindir; eşzamanlı gömülü kullanım güvencesi yoktur.
G37 dosyası bu işlemde değiştirilmez. G39 CLI dış dosya/yol/endpoint almaz;
Python API'sindeki günlük dizini de yalnız offline test verisi için kullanılmalıdır.

## Çalıştırma ve test sonuçları

```sh
python3 -B offline_safety_chain_g39.py --scenario normal
python3 -B offline_safety_chain_g39.py --scenario all
python3 -B -m unittest discover -s tests -p test_offline_safety_chain_g39.py -v
python3 -B -m unittest discover -s tests -v
```

CLI `normal` → çıkış 0; olumsuz senaryo ve `all` → çıkış 2. Kod 0 yalnız
manuel incelemeye uygun rapordur, giriş/kilit açma izni değildir. Gerçek CLI
alt süreçleri ve 19 senaryonun tümündeki `entry_authorized=false`,
`unlock=false`, `stop_unchanged=true` koşulları test edildi.

| Son doğrulama | Toplam | Başarılı | Hata | Atlama | Çıkış |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hedefli G39 | 52 | 52 | 0 | 0 | 0 |
| Tam paket | 486 | 484 | 2 | 0 | 1 |

Son hedefli koşu: 5,070 s. Ardından son tam paket: 8,348 s.
İlk tam koşu da aynı 486 test ve aynı iki hatayla sonuçlandı.
Tam paketteki iki hata önceden bildirilen eksik bağımlılıkla örtüşür:

1. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
2. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError: No package metadata was found for binance-sdk-derivatives-trading-usds-futures`.

Hatalar saklanmadı, skip edilmedi ve testler değiştirilmedi. Paket kurulmadı.
Günlükler:

- `/private/tmp/g39-safety-chain-target-tests-20261110.log`
- `/private/tmp/g39-safety-chain-full-tests-20261110.log` (ilk tam koşu)
- `/private/tmp/g39-safety-chain-full-tests-final-20261110.log` (son tam koşu)

## Açık işler, riskler ve sonraki güvenli adım

- G37/G38 onaylanmamış offline modüllerdir; G39 bu statüyü değiştirmez.
- Güvenilen Python süreci ve POSIX yerel dosya sistemi varsayılır. Frozen
  nesneler düşmanca Python koduna karşı sandbox değildir. Mock nesne kimliği
  gerçek imza değildir. SHA256 zinciri harici güven kökü/WORM sağlamaz;
  disk sahibinin geçmişi yeniden yazmasını veya geçerli sonu silmesini kanıtlayamaz.
- G38 journal'ı yalnız bellek modelidir; gerçek owner kilidi, disk güvenliği,
  canlı saat veya atomik owner işlemi güvencesi yoktur. G39 simülasyonu hiçbir
  canlı Binance doğrulaması veya production uygunluğu iddiası taşımaz.
- Başka worktree, canlı FAST/owner SDK, Keychain, kimlik bilgileri ve yasak
  sistem dizinlerine erişilmedi; ağ veya özel Binance çağrısı yapılmadı.
- Sonraki güvenli adım Claude'un bu üç yeni dosyayı bağımsız incelemesi ve
  hedefli/tam testleri kendisinin çalıştırmasıdır. Claude incelemesi bekliyor;
  çapraz inceleme tamamlandı iddiası yoktur.
- Bu offline teslim için ek kullanıcı onayı beklenmiyor. Kritik/production
  değişikliği, entegrasyon, commit/push/merge/PR bu görev kapsamında yetkili değildir.
