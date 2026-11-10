# G52 — Paylaşılan WAL writer fence çevrimdışı denetimi

Tarih: 10.11.2026. Bağlam: ATLAS FAST G52 / kullanıcı tarafından verilen
GitHub Issue #17 görevi. Issue/ağ okunmadı. Worktree:
`/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g52`. Dal:
`codex/g52-wal-fence-audit`. Baz ve değişmeyen HEAD:
`e682b396a65b3d67a631d8d041e74dfe07aeac3a`.

**Sonuç: paylaşılan owner WAL fence UNPROVEN; resolve yasağı korunuyor.**
G52 hedefli testleri 86/86 başarılıdır. Tam paket 1063 testte 2 SDK hatasıyla
başarısızdır; tam doğrulama tamamlandı/yeşil olarak sunulmaz. Bütün G52
kararlarında `unlock=false`, `entry=false`, `resolve=false` sabittir.

## Kapsam ve dosyalar

Önce `AGENTS.md`, `G44_A_ENTEGRASYON_DENETIM.md`,
`G45_ENTEGRASYON_SOZLESMESI.md` ve owner'ın ilgili statik kaynakları okundu.
Dal, baz ve temiz başlangıç doğrulandı. Mevcut kaynak/testler salt okunur
kaldı. Yalnız üç yeni dosya oluşturuldu:

- `wal_shared_fence_audit_offline_g52.py`: AST raporu, kapalı karar tipi,
  10 satırlık kabul matrisi, temp-dir stdlib fake-process laboratuvarı ve
  yalnız yerleşik sentetik senaryoları kabul eden CLI.
- `tests/test_wal_shared_fence_audit_offline_g52.py`: 86 ayrı unittest;
  bazı testlerde her rol/kabul koşulu için ek subtest'ler vardır.
- `G52_DEVIR.md`: bu teslim, kaynak bulguları ve test sınırı.

Yeni commit yoktur. Kullanıcının özel talimatıyla commit/push/merge/PR,
`DEVIR_NOTU.md` değişikliği ve diğer worktree'lere erişim yapılmadı. Yukarıdaki
kimlik değişiklik commit'i değil denetim bazıdır. G51 Claude bulgusu görev
girdisi olarak alındı; Claude/alt ajan çağrılmadı, yeni bağımsız inceleme
yapılmış sayılmaz. G52 üretim entegrasyonu veya kritik owner yaması değildir.

## Statik dayanak ve writer katılımı

Okunan `owner_single_execution.py` SHA256:
`612c6c2b41da37b06014e4057355c01cd56e5ef09132f66d6a841eefea4cb6ea`.
Satırlar bu bazdaki dosyaya aittir. G52 owner'ı import etmez; AST'sini
compile/exec/eval etmez. `audit_source()` yalnız verilmiş metni ayrıştırır;
dosya yolu, callback veya çalışan owner nesnesi almaz.

| Writer ailesi | Statik kaynak işareti | Katılım kararı / sınır |
| --- | --- | --- |
| ENTRY | `run_once:1527` → `one_write`; lease acquire `664`, journal `674`, post `679`, release `685` | UNPROVEN; lexical sıra görünür, runtime scope/otorite kanıtı yok. |
| STOP | `place_stop_write:708` → `one_write(...,"STOP",...)` → journal `674` | UNPROVEN; aynı yerel sıra tüm dış writer'ları kapsamaz. |
| TP1 / TP2 | `run_once:1584` zip rol döngüsü → `one_write:1585` → journal `674` | UNPROVEN; dinamik rol bağlama caller incelemesi gerektirir. |
| CANCEL_STOP | `cleanup_owned_exits:764` journal; delete `766` | UNPROVEN; delete içindeki lease, önceki journal'ı kapsıyor sayılamaz. |
| CANCEL_TP | `cleanup_owned_exits:777` dinamik `CANCEL_TP` + index; delete `780` | UNPROVEN; mock'ta CANCEL_TP1 ve CANCEL_TP2 ayrı sınanır. |
| EMERGENCY | `_emergency_flatten_locked:736` journal; post `738` | UNPROVEN; ayrı emergency lock veya post lease'i ortak journal fence kanıtı değildir. |
| POLICY_RESERVE | `write_policy_reserve_record:1141` writable open; dosya fsync `1145`, dizin fsync `1147` | UNPROVEN; O_EXCL/fsync işaretleri tüm writer'ları dışlamaz. |
| incident | `emergency_note:200` writable open; dosya fsync `203` | UNPROVEN; bu fonksiyonda dizin fsync çağrısı görülmez; wrapper/runtime davranışı ayrıca bilinmez. |

`journal:143` içinde dosya fsync `156`, dizin fsync `158` görünür. Rapor,
salt okunur dizin `os.open` çağrısını WAL yazarı diye sınıflandırmaz. Bunlar
yalnız AST çağrı işaretleridir; fsync'in başarıyla döndüğü, güç kesintisi
dayanıklılığı veya gerçek owner'ın OS filelock kullandığı iddia edilmez.

Üç ayrı kavram korunur:

1. **Global owner lock:** `global_one_trade_lock:161`, `fcntl.flock:175` ve
   `main:1645` edinimi / `1649` bırakımı kaynakta görünür. Owner giriş ömrü
   tekilliği, tüm WAL writer'larının ve resolver'ın aynı kritik bölümde
   olduğunu kanıtlamaz.
2. **Mutation lease:** `SignedVenue.acquire_mutation_lease:363` dış
   `owner_mutation_fence` modülüne, `_mutation_once:389` venue dispatch'ine
   bağlıdır. SDK yazım sınırı ile journal başlangıcı aynı sınır sayılamaz.
3. **Shared writer fence:** bütün writer aileleri + resolver aynı otorite,
   scope ve kalıcı epoch/generation ile son kontrol/yazım sınırına katılmalı.
   Bu görevde böyle bir entegrasyon kanıtlanmadı veya uygulanmadı.

Dynamic import, alias/monkey-patch, getattr/setattr ve wrapper dispatch
false negative'leri için `UNPROVEN` üretilir. Bu sınırlamalar, kaynakta
dinamik çağrı bulunmasa bile korunur; AST taraması kapalı bir dünya değildir.
İç fonksiyon çağrıları dış fonksiyon çalışmış gibi sayılmaz. Toplanan yerel
çağrı listesi eksiksiz çalışma zamanı callgraph'ı değildir. Kaynakta fence
importu bulunması veya aramada bulunmaması tek başına katılım kanıtı değildir.

## Resolve engeli

`mark_scope_resolved:237` yalnız docstring ve
`raise Blocked("WAL_RESOLUTION_MARKER_DISABLED_SHARED_FENCE_UNPROVEN")`
içerir. Bu statik biçim gözlendi; fonksiyon ve çağıranları değiştirilmedi.
Caller'lar `risk_ledger_entry_gate:1166`, `_restart_one:1258`,
`run_once:1625` olarak görünür. Her biri aynı kapalı marker sınırına ulaşır.

Zero-entry yolunda `1115` WAL tekrar okuması sonrası `1117` hâlâ
`ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN` ile durur. İki eşit
snapshot, ikinci okumadan sonra incident/ENTRY yazılmasını dışlamaz; ABA
nedeniyle aynı görünüm farklı otorite nesline de ait olabilir. Signed FLAT,
ledger CLOSED, normal lease veya olumlu mock ACK bu engeli açmaz. G52'nin
`resolve_request()` fonksiyonunda marker yazımı yoktur.

## Fail-closed kabul matrisi

Her koşul için `False → FAIL`, eksik kanıt (`None`) → `UNPROVEN`, `True →
PASS_SYNTHETIC_ONLY`. Eksik/fazla anahtar, string/int truthiness ve bozuk
şema → FAIL. Olumlu satır ve toplam sonuçta bile üç yetki alanı false'tur.
Bu durumlar owner güvenliğine ilişkin PASS anlamına gelmez.

| Koşul | Negatif kanıt / G52 test kapsamı | Gerçek owner kabul durumu |
| --- | --- | --- |
| all_writers | Her rolün nonparticipant yazımı; incident mock fence tutulurken de yazar | UNPROVEN |
| one_authority | Global lock/mutation lease adını writer otoritesi sayma; başka lab'ın eşit sayaçlı token'ı | UNPROVEN |
| atomic_mutation | Başarılı precheck ardından release/yeni holder/geç commit → ret | UNPROVEN |
| durable_epoch | Restart'ta eski epoch token'ı; olumlu restart replay'i yalnız mock | UNPROVEN |
| no_aba | Aynı holder yeniden edinimi; aynı boş snapshot/farklı nesil; bool/float token alanları | UNPROVEN |
| resolver_quiescence | İki eşit okumadan sonra late incident; resolver grant hiçbir marker üretmez | UNPROVEN |
| lock_order | Farklı otorite ve contention reddi; gerçek nested-lock sırası/deadlock denenmedi | UNPROVEN |
| file_and_directory_fsync | Append öncesi, torn/after-write, file-fsync sonrası, directory-fsync sonrası, ACK öncesi crash; iki fsync hata enjeksiyonu | UNPROVEN |
| anchored_replay | Boş/torn WAL, duplicate JSON key/kayıt, reorder, eksik ara/son kayıt, hash/schema/kimlik ve pozitif epoch/generation gerilemesi | UNPROVEN |
| unknown_no_retry | UNKNOWN sonrasında aynı grant ile tekrar ve mevcut dosyayı overwrite reddi | UNPROVEN |

Mock laboratuvar kendi temp dizininde `xb`, flush, dosya/dizin fsync kullanır;
gerçek owner/WAL/STOP/global lock'a erişmez. `FakeProcess` iki **bellek içi
aktördür**. Yarışlar deterministik çizelgedir: iki OS süreci/thread, gerçek
filelock, kill veya güç kesintisi testi değildir. Check + mutation'ın tek
sıralı model adımı olması varsayımdır; sert deadline, starvation/deadlock
ve gerçek storage linearization kanıtı üretilmez. Epoch bellek içidir.

Replay'deki anchor caller'ın verdiği mock count/hash'tir; bağımsız kalıcı
güven kökü değildir. Anchor yokken geçerli zincir bile UNPROVEN; suffix
kaybı kanıtlanamaz. ACK öncesi crash'te dosya mevcut olabilir; replay'in
olumlu mock sonucu bilinmeyen işleme yeni izin veya retry vermez. Hash ve
O_EXCL tek başına kötü niyetli yeniden yazma/truncation'a dayanıklılık değildir.

## Test komutları ve ölçülen sonuçlar

```sh
python3 -B -m unittest discover -s tests -p test_wal_shared_fence_audit_offline_g52.py -v
python3 -B -m unittest discover -s tests -v
python3 -B wal_shared_fence_audit_offline_g52.py --scenario all
```

| Nihai koşu | Test | Başarılı | Failure | Error | Skip | Süre / çıkış |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| G52 hedefli | 86 | 86 | 0 | 0 | 0 | 0.405 s / 0 |
| Tam paket | 1063 | 1061 | 0 | 2 | 0 | 17.027 s / 1 |

CLI smoke: 5 yerleşik sentetik senaryo, 10 kabul satırı; sonuç UNPROVEN,
üç yetki alanı false. Kaynak/WAL yolu, unlock veya resolve argümanı
kabul edilmediği negatif testlerle doğrulandı. CLI çıkış 0 yalnız JSON raporu
üretildiğini belirtir. Owner kaynağını CLI'den okumak mümkün değildir.

Tam paketteki iki hata saklanmadı, skip edilmedi ve bağımlılık kurulmadı:

- `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
  `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
- `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
  `PackageNotFoundError: No package metadata was found for binance-sdk-derivatives-trading-usds-futures`.

Koşu öncesi stdlib metadata ve üst düzey `find_spec` ile SDK ve
binance-common'un bulunmadığı doğrulandı; SDK import edilmedi. Mevcut SDK
contract testi import denemesinde yukarıdaki hatayı verdi; gerçek SDK
yüklü/çalışır değildi. Mevcut suite bazı owner AST parçalarını sahte
namespace'te yürütür ve kendi temp-dir süreç/filelock/marker fixture'larını
çalıştırır; bunlar G52 statik denetleyicisinin veya gerçek owner'ın güvenlik
kanıtı olarak sunulmaz. Test/suite hiçbir mevcut dosya değiştirilerek
yeşile çevrilmedi. Önceki tam koşu 1062 test / 1060 başarılı / aynı 2 hata /
0 failure / 0 skip / 16.383 s idi; son ek statik doğrulama testi sonrası
yukarıdaki nihai ölçüm alındı.

Yerel geçici tam çıktı dosyaları (kalıcı teslimin parçası değildir):

- Hedefli: `/var/folders/3h/k6rc668s5y7f5d5lmhvs3w1w0000gn/T/g52-hedefli-h8q201nw.log`
- Nihai tam: `/var/folders/3h/k6rc668s5y7f5d5lmhvs3w1w0000gn/T/g52-tam-f575qxo5.log`
- Önceki tam: `/var/folders/3h/k6rc668s5y7f5d5lmhvs3w1w0000gn/T/g52-full-suite-_fixotcc.log`

## Açık durum ve sonraki güvenli adım

Tam suite SDK eksikleri nedeniyle yeşil değildir. Gerçek ortak writer
fence, kalıcı generation, OS concurrency, crash/kill, fiziksel durability,
tam callgraph ve lock-order doğrulaması açıktır. Pozitif test sayısı bunları
kapatmaz. Kullanılmayan mevcut kod silinmedi; temizlik yapılmadı.

Sonraki güvenli adım bu üç offline dosyanın kullanıcı tarafından
değerlendirilmesidir. Bu teslim için ek onay gerekmiyor. Gerçek owner
entegrasyonu, resolve açılması veya kilit davranışı değişikliği için bu
görev yetki vermez; böyle bir çalışma ayrı görev ve bağımsız doğrulama
gerektirir. G51 incelemesi G52 için yeni onay olarak gösterilmez.

Ağ, private API, canlı SDK, canlı log/WAL, `/Users/Shared/Atlas`,
`~/Atlas/AKTIF_*`, Keychain/.env/secret, gerçek emir veya owner/STOP kilidi
üzerinde işlem yapılmadı. Son Git kontrolleri takipli/staged delta olmadığını,
yalnız izin verilen üç yeni dosyayı gösterir; dal ve HEAD değişmemiştir.
