# Görev 36 — Offline TP gözlem provenance adayı

Tarih: 10 Kasım 2026. Çalışma alanı:
`/Users/hakanyilmaz/Atlas/FAST_DEV_codex`.
Dal: `codex/f00-ortam`.
Başlangıç ve rapor anındaki HEAD:
`349e772593d7332fa1838af6b268610d6dbcae4c`.
**Bu görevde commit, push, merge veya PR yapılmadı. Yeni commit kimliği yok.**
Aşağıdaki eski çalışma kaydındaki gönderim/kurulum yetkileri bu görev için
uygulanmadı; güncel kullanıcı talimatı paket ve network kullanımını yasaklıyor,
commit/push öncesinde sonuç raporu istiyor.

## Tamamlanan işler ve dosyalar

- `AGENTS.md`, mevcut test dizini ve offline gözlem modelleri incelendi.
- Yeni `tp_observation_provenance_candidate.py`: yalnız bellekte önceden
  yüklenen sentetik kayıtları okuyan `MockSignedReadSource`, değişmez
  `MockReadReceipt` ve tek TP kimliği için sıralı inceleyici eklendi.
- Yeni `tests/test_tp_observation_provenance_candidate.py`: 50 bağımsız unittest.
- `DEVIR_NOTU.md`: bu güncel görev kaydı eklendi; eski teşhis kaydı korundu.
- Başlangıçta izlenmeyen `tp_provenance_offline.py` vardı. Çalışma sırasında
  ayrıca `TP_PROVENANCE_OFFLINE_G36.md`, `tests/test_tp_provenance_offline.py`
  ve `tests/test_tp_safety_integration_offline.py` görünür oldu. Bu dört dosya
  bu çalışma tarafından oluşturulmadı veya değiştirilmedi. Tam keşif testinde
  diğer iki test dosyasındaki 26 test de çalıştı; 416 toplamı bunları içerir.

## Sağlanan sınırlı bağlama ve güvenlik invariants

- Token yalnız mock kaynağın `read` işlevinde oluşturulur ve aynı kaynakta
  kayıtlı makbuzun nesne kimliğine bağlanır. İstemci tarafından oluşturulan
  boolean, kaynak etiketi, token taklidi, başka kaynak makbuzu veya orijinal
  token ile yeniden oluşturulan/değiştirilen makbuz kabul edilmez.
- Scope, symbol, order-id, client-order-id ve TP1/TP2 rolü beklenen kimlikle;
  timestamp, status, quantity ve executed-quantity kaynak kopyasıyla eşleşir.
  Eksik/fazla alanlar, geçersiz tipler, boolean ID/zaman, NaN/sonsuz/negatif
  miktar ve tutarsız fill durumu reddedilir.
- Eski/gelecek gözlem veya okuma zamanı ve gözlemden önceki okuma reddedilir.
  Kabul edilen makbuz kaynakta tüketilir; ikinci inceleyici de aynı token'ı
  tekrar kullanamaz. Tek inceleyicide duplicate, eski sıra, aynı zamandaki
  çelişki, fill gerilemesi, miktar değişikliği ve terminal sonrası gözlem
  reddedilir. Ret, token veya kabul geçmişini ilerletmez.
- Normal/conditional açık emir listeleri ve STOP OPEN/MISSING/FILLED/CANCELED/
  UNKNOWN/AMBIGUOUS durumları yalnız tanısaldır. Boş listeler ve terminal STOP
  etiketi dahil her sonuçta `entry_authorized`, `may_clear_latch`,
  `may_release_owner_lock`, `may_submit_order`, `may_modify_stop` ve
  `may_retry_wal` sabit `False` kalır.
- İmza mekanizması veya gerçek Binance verisi doğrulanmadı.
  `authenticated_signed_get_verified` ve `real_exchange_data_verified`
  her zaman `False`. Token kriptografik kanıt veya Python iç durumuna
  müdahaleye dayanıklı bir güvenlik sınırı değildir; fixture'ları çağıran seçer.
- Adayın I/O, callback, transport, SDK, Owner, risk veya WAL bağlantısı yok.
  ENTRY/STOP/TP yapısı ve WAL NO_RETRY davranışı değiştirilmedi.

## Çalıştırılan testler ve sonuçları

| Komut | Sonuç | Çıkış kodu |
| --- | --- | --- |
| `python3 -B -m unittest discover -s tests -p test_tp_observation_provenance_candidate.py -v` | 50/50 geçti; 0.011 sn | `0` |
| `TMPDIR="$PWD/.gorev36_test_tmp" python3 -B -m unittest discover -s tests -v` | 416 test: 414 geçti, 2 hata; 6.125 sn | `1` |

Her iki çalışmada assertion başarısızlığı ve atlanan test sayısı sıfır.
Tam test kümesi başarılı değildir; bilinen iki dış bağımlılık hatası sürdü:

1. `InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
2. `InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError: No package metadata was found for binance-sdk-derivatives-trading-usds-futures`.

Paket kurulmadı; test kaldırılmadı, değiştirilmedi, atlanmadı veya sahte SDK
ile geçer hale getirilmedi. Tam testin geçici dosyaları `TMPDIR` aracılığıyla
yalnız bu worktree içinde tutuldu. Test sonunda boş geçici dizin kaldırıldı.

## Açık konular, inceleme ve sonraki güvenli adım

- Tam test kümesinin iki bağımlılık hatası açık; tüm testler geçti denilemez.
- Kaynak ve inceleyici yalnız tek süreçte sıralı kullanım içindir. İlerleme
  geçmişi inceleyiciye, tüketilmiş token geçmişi kaynak örneğine aittir.
  Yeni kaynak/inceleyici kalıcı veya küresel duplicate/replay koruması sağlamaz;
  bu aday canlı sisteme bağlanacak bir doğrulayıcı olarak değerlendirilmemeli.
- Kullanıcı talimatıyla Claude/başka ajan çalıştırılmadı; bağımsız code review
  henüz yapılmadı ve son aşamayı bekliyor.
- Kritik mevcut dosyalar ile risk/Owner modülleri değiştirilmedi. Canlı API,
  credentials, Keychain, yasak canlı dizinler, Documents offline verileri,
  servisler ve gerçek STOP/guard durumları kullanılmadı veya değiştirilmedi.
- Kullanılmayan kod temizliği kapsam dışındadır; mevcut modül silinmedi.
- Sonraki güvenli adım: bu adayın ve 50 testin kullanıcı tarafından gözden
  geçirilmesi, ardından kullanıcının belirleyeceği bağımsız kod incelemesi.
  Commit/push bu rapor öncesinde yapılmadı; merge kullanıcıya aittir.
  Paket kurulumu, network, canlı entegrasyon veya ek işlem için yetki yok.

---

# Önceki çalışma kaydı: Görev F-00/A — Test ortamı teşhisi

Tarih: 10 Kasım 2026. Çalışma alanı: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex`.
Dal: `codex/f00-ortam`; başlangıç dalı: `codex/baslangic`.
Başlangıç commit'i: `51d0965b8f7dd6858717125e89a1ed6daa071d50`.

## Tamamlanan inceleme ve mevcut durum

Tam test kümesi çalıştırıldı; **340 testin 338'i geçti, 2'si hata verdi**.
Başarısız assertion yok; atlanan veya devre dışı bırakılan test yok.
**340/340 elde edilmedi; test ortamı henüz hazır değil.** Bu çalışma teşhis ve
kurulum planıyla sınırlıdır; paket kurulmadı ve `.venv` oluşturulmadı.

İki hatanın doğrudan nedeni, testi çalıştıran Python ortamında Binance SDK
paketlerinin bulunmamasıdır. Mevcut hata izleri transport uygulamasında bir
kusur göstermiyor: testler SDK import'u ve dağıtım metadata'sı aşamasında
duruyor. Paketler kurulmadan SDK arayüzünün bu Python sürümünde çalıştığı veya
bütün testlerin geçeceği sonucuna varılamaz.

## Ortam kanıtı

- `python3` yolu: `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`.
- Python sürümü: `3.13.15`.
- `sys.prefix` ve `sys.base_prefix` eşit:
  `/Library/Frameworks/Python.framework/Versions/3.13`. Test sanal ortamda çalışmadı.
- Bu yorumlayıcıda hem `binance-sdk-derivatives-trading-usds-futures` hem de
  `binance-common` için `importlib.metadata.version(...)`, ayrı ayrı
  `PackageNotFoundError` verdi.
- `importlib.util.find_spec(...)`, hem
  `binance_sdk_derivatives_trading_usds_futures` hem de `binance_common` için
  `None` döndürdü. Hem dağıtım metadata'sı hem import edilebilir paket eksik.
- Worktree kökünde `.venv` yok. Mevcut `.gitignore:4`, `.venv/` dizinini dışlıyor.

Buradaki “sistem Python'u”, kabuktaki `python3` komutunun seçtiği yukarıdaki
yorumlayıcıdır; `/usr/bin/python3` üzerinde bir inceleme yapılmadı.

## Hataların kaynak koduyla eşleştirilmesi

1. `InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`
   (`tests/test_sdk_transport_offline.py:142`):
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
   Hata `DerivativesTradingUsdsFuturesRestAPI` import edilirken oluşuyor;
   metot/imza assertion'larına henüz ulaşılmıyor. Test `query_order`,
   `account_information_v3`, `current_all_open_orders`,
   `current_all_algo_open_orders` metotlarını; `query_order` için `symbol`,
   `orig_client_order_id`, `recv_window` ve `account_information_v3` için
   `recv_window` parametresini bekliyor.
2. `InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`
   (`tests/test_sdk_transport_offline.py:137`):
   `PackageNotFoundError: No package metadata was found for binance-sdk-derivatives-trading-usds-futures`.
   Test `source_const("SDK_PINS")` değerlerini AST üzerinden okuyup
   `importlib.metadata.version(pkg)` ile karşılaştırıyor. Döngü ilk eksik
   pakette duruyor; ayrıca yapılan metadata kontrolü `binance-common` paketinin
   de eksik olduğunu doğruladı. Bu, sürüm eşitliği assertion hatası değil.

`OwnerTransportContractTests` içindeki **12/12** test geçti. Bu testler
`owner_single_execution.py` dosyasını import etmiyor; `_order_not_found` ve
`make_guarded_request` işlevlerini AST üzerinden seçip sahte HTTP transport'u
ile çalıştırıyor. SDK istemcisi oluşturulmuyor. SDK test dosyası tek başına
çalıştırıldığında da aynı iki hata alındı; sorun başka testlerin çalışma
sırasından kaynaklanmıyor.

## Kodun beklediği SDK paketleri ve tam sürümleri

Yetkili kaynak: `owner_single_execution.py:77` içindeki `SDK_PINS`.
Sabit AST ile okunarak doğrulandı; Owner yürütücü modülü import edilmedi.

| Dağıtım paketi | Tam sürüm | Sürüm kontrolü hata kodu |
| --- | --- | --- |
| `binance-sdk-derivatives-trading-usds-futures` | `17.6.0` | `SDK_VERSION_NOT_PINNED` |
| `binance-common` | `4.5.1` | `SDK_COMMON_VERSION_NOT_PINNED` |

`SDK_OFFLINE_INTEGRATION_20261105.md` aynı sürümleri tarihsel olarak kaydediyor;
bu belgedeki eski ortam ve test sonucu bugünkü worktree için kanıt sayılmadı.
Başka bir sanal ortam kullanılmadı veya kopyalanmadı.

## Kullanıcı onayından sonra uygulanacak yerel kurulum planı

**Aşağıdaki komutlar yalnızca plandır; bu görevde çalıştırılmadı.**
Onay kapsamı, iki sabit SDK paketi ve bunların paket metadata'sında tanımlı
zorunlu geçişli bağımlılıklarının yalnızca bu worktree'nin `.venv` dizinine
kurulması olmalıdır. Kaynak kod geçişli bağımlılıkların tam sürümlerini
tanımlamıyor; bunlar için doğrulanmış bir kilit dosyası olduğu iddia edilmiyor.

Mevcut Python `3.13.15` kullanılarak, sistem paketlerine erişmeyen standart
bir sanal ortam oluşturulacak. `--system-site-packages` kullanılmayacak;
aktivasyon yerine her komutta doğrudan yerel yorumlayıcı seçilecek.
Sistem Python'una kurulum veya ayrı bir `pip` yükseltmesi gerekmiyor.

```sh
cd /Users/hakanyilmaz/Atlas/FAST_DEV_codex
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m venv .venv
./.venv/bin/python -m pip --isolated --disable-pip-version-check install --index-url https://pypi.org/simple 'binance-sdk-derivatives-trading-usds-futures==17.6.0' 'binance-common==4.5.1'
./.venv/bin/python -m pip --isolated --disable-pip-version-check check
./.venv/bin/python -B -c 'import sys; from importlib.metadata import version; print(sys.executable); assert sys.prefix != sys.base_prefix; assert version("binance-sdk-derivatives-trading-usds-futures") == "17.6.0"; assert version("binance-common") == "4.5.1"'
./.venv/bin/python -B -m unittest discover -s tests -p test_sdk_transport_offline.py -v
./.venv/bin/python -B -m unittest discover -s tests -v
./.venv/bin/python -m pip --isolated --disable-pip-version-check freeze > .venv/f00-ortam-freeze.txt
```

Adımlar sırayla yürütülecek; herhangi biri başarısız olursa sonraki adıma
geçilmeden hata incelenecek. Pip'in Python 3.13 ve iki sabit sürüm için
bağımlılık çözümü bu görevde denenmedi. Uyuşmazlık çıkarsa sürüm sabitleri veya
testler değiştirilmeden sonuç raporlanacak. `freeze` çıktısı başarılı kurulumun
geçişli sürümlerini yerel olarak kaydeder; önceden doğrulanmış bir kilit dosyası
yerine geçmez. `.venv` Git'e eklenmeyecek.

Kabul ölçütü: `pip check` başarılı, paket sürümleri birebir aynı, SDK dosyası
**14/14**, tam test kümesi **340/340**, hata/başarısızlık/atlama yok. Sonuç
yalnızca bu gerçek yerel kurulumla yapılan yeni test çalışmasından alınacak;
SDK paketlerinin yerine sahte modül veya sahte metadata konulmayacak.

## Çalıştırılan testler ve sonuçları

| Komut | Sonuç | Çıkış kodu |
| --- | --- | --- |
| `python3 -B -m unittest discover -s tests -v` | 340 test, 338 geçti, 2 hata; 1.039 sn | `1` |
| `python3 -B -m unittest discover -s tests -p test_sdk_transport_offline.py -v` | 14 test, 12 geçti, aynı 2 hata; 0.005 sn | `1` |

Her iki çalışmada assertion başarısızlığı ve atlanan test sayısı sıfırdır.
Geçici tam çıktılar: `/tmp/f00-ortam-unittest-20261110.log` ve
`/tmp/f00-ortam-sdk-tests-20261110.log`. Bu yollar yereldir; kalıcı bulgular ve
hata türleri yukarıda kaydedildi.

## Değişiklik kapsamı, açık konular ve sonraki güvenli adım

- Değiştirilen tek depo dosyası: `DEVIR_NOTU.md` (bu teşhis ve kurulum planı).
  Uygulama kodu, testler, sürüm sabitleri ve güvenlik kontrolleri değiştirilmedi.
- Kullanılmayan kod değerlendirmesi bu ortam teşhisinin kapsamında değil;
  hiçbir modül silinmedi.
- Canlı sistem, `AKTIF_*` klasörleri, anahtarlar, Keychain veya canlı Binance
  hesap/emir uçları kullanılmadı. Servis veya bot işlemi yapılmadı.
- Açık engel: yerel SDK kurulumu için kullanıcı onayı bekleniyor. Tam test
  kümesi mevcut durumda kırmızı; işlevsel test başarısı ilan edilmiyor.
- Sonraki güvenli adım: kurulum planının kullanıcı tarafından onaylanması,
  ardından yalnızca bu `.venv` üzerinde kurulum ve yukarıdaki doğrulamalar.
- Kod geliştirilmediği için bu görevde kod çapraz incelemesi yapılmadı.
  Dal için GitHub Pull Request açılması önerilir; birleştirme kullanıcıya aittir.
- Gerekli onay: paket kurulumu. Kurulum bu görevde talep edilmedi veya yapılmadı.
  Bu dala commit ve `git push origin codex/f00-ortam` kullanıcı tarafından
  zaten yetkilendirildi; ana dala gönderim veya birleştirme yapılmayacak.

## Commit kaydı

- Dal: `codex/f00-ortam`.
- Teşhis ve kurulum planı commit'i:
  `296bcc59b16d04e8017aef08a6f15164bfa6afb2`.
- Bu kimliği devir notuna ekleyen sonraki commit yalnızca kayıt güncellemesidir.
  Bir commit kendi kimliğini kendi içeriğine yazamayacağı için son kayıt
  commit'i `git log -1 --format=%H -- DEVIR_NOTU.md` ile bulunabilir; kimliği
  kullanıcıya son raporda ayrıca verilecektir. Commit amend/rebase yapılmadı.
- Gönderim hedefi: `origin` → `hknylmzxx-source/atlas-fast-dev`,
  `refs/heads/codex/f00-ortam`; komut: `git push origin codex/f00-ortam`.
  Gönderim sonucu ve son commit kimliği işlem sonrasında ayrıca doğrulanacaktır.

---

# Görev 36 — Ek kayıt: TP olay/plan/bağımsız okuma ve birleşik testler

Tarih: 10 Kasım 2026. Dal: `codex/f00-ortam`.
Bu çalışmanın başlangıç ve son kontrol HEAD'i:
`349e772593d7332fa1838af6b268610d6dbcae4c`.
Bu bölüm, yukarıdaki başka çalışmanın Görev 36 kaydından ayrı bir çalışma
kapsamını belgeler; yukarıdaki kayıtlar değiştirilmeden korunmuştur.

## Bu çalışmada geliştirilen dosyalar

- `tp_provenance_offline.py`: planın kopyasıyla TP olayı ve ayrı mock emir
  okumasının kimlik, yön, fiyat, miktar, dolum ve zaman eşleşmesi. Sahte
  kaynak/onay etiketleri veya gömülü makbuz bağımsız okumanın yerine geçmez.
  Eski/gelecek olaylar, tekrar, dolum gerilemesi, kimlik değişimi ve FILLED
  sonrasında yeniden açılma reddedilir. Ret, kabul geçmişini ilerletmez.
- `tests/test_tp_provenance_offline.py`: 17 test; TP1/TP2 ve BUY/SELL,
  bozuk sayılar/tipler, zaman, replay ve bağımsız makbuz senaryoları.
- `tests/test_tp_safety_integration_offline.py`: 10 test; başka yerel sürecin
  POSIX fence'i, symlink/hardlink reddi, incident/TP WAL varken çözüm engeli;
  AST ile seçilmiş Owner işlevlerinde kesin GET, belirsiz makbuzda tek mock
  POST, meşgul fence altında sıfır mock POST ve lease'in bırakılması.
- `TP_PROVENANCE_OFFLINE_G36.md`: davranış, test kapsamı ve sınırlamalar.
- `DEVIR_NOTU.md`: yalnız bu ek kayıt.

Yeni paket eklenmedi, kullanılmayan kod temizliği yapılmadı, modül silinmedi.
Üretim bağlantısı yoktur. Başarılı sonuç yalnız sentetik tutarlılık demektir;
gerçek Binance imzası/hesabı/kaynağı doğrulanmaz. Her sonuçta
`authenticated_signed_get_verified`, `entry_authorized`, `may_clear_latch`
ve `may_release_owner_lock` sabit False'dur. Denetleyici disk/ağ kullanmaz;
enjekte edilen callback yalnız mock/replay olmalıdır, kendi başına ağ erişim
engeli değildir. Tekrar belleği kalıcı değildir; eşzamanlı kullanım kapsam
dışıdır. Aynı milisaniyede farklı olaylar ve olay/okuma arasındaki yeni dolum
muhafazakâr biçimde reddedilir.

## Bu çalışmanın test sonuçları

| Komut/çalıştırma | Sonuç | Çıkış |
| --- | --- | --- |
| Başlangıç `python3 -B -m unittest discover -s tests -v` | 340 test: 338 geçti, 2 SDK hatası; 1.055 sn | 1 |
| Son `python3 -B -m unittest discover -s tests -p 'test_tp_*offline.py' -v` | **27/27 geçti**; 0.056 sn | 0 |
| Ara tam keşif (dış aday eklenmeden) | 366 test: 364 geçti, aynı 2 SDK hatası; 1.101 sn | 1 |
| Son `python3 -B -m unittest discover -s tests -v` | **417 test: 415 geçti, 2 SDK hatası**; 1.110 sn | 1 |
| `git diff --check` | Başarılı | 0 |

Assertion başarısızlığı ve atlanan/devre dışı bırakılan test yoktur.
Tam küme yeşil değildir; işin tüm kabul şartları sağlandı denilemez.
Hatalar başlangıçtaki aynı iki testtir:

- `InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
  eksik `binance_sdk_derivatives_trading_usds_futures` nedeniyle
  `ModuleNotFoundError`.
- `InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
  eksik SDK dağıtım metadata'sı nedeniyle `PackageNotFoundError`.

Python 3.13.15 ortamında SDK 17.6.0 ve binance-common 4.5.1 kurulu değil,
`.venv` yok. AGENTS.md madde 5 uyarınca yerel paket kurulumu için kullanıcıya
soru iletildi; yanıt/onay alınmadığından kurulum yapılmadı.

Loglar: `/tmp/atlas-g36-baseline-tests.log`,
`/tmp/atlas-g36-target-tests.log`, `/tmp/atlas-g36-full-tests.log`,
`/tmp/atlas-g36-final-full-tests.log`.

## Bağımsız çapraz inceleme

AGENTS.md madde 6 gereği `g36_capraz_inceleme` adlı bağımsız Codex ajanı
yalnız bu çalışmanın dört yeni dosyasını inceledi ve testleri kendisi
çalıştırdı; depo dosyası değiştirmedi. İncelemede okuma zamanının olayın
yayımından önce olabildiği P2 tutarsızlığı bulundu. Olayın `E` zamanı
immutable kayda eklendi, bağımsız okuma zamanının `E` öncesinde olmasına
ret zorunlu kılındı ve regresyon testi eklendi. İnceleyen ajan düzeltmeyi
bağımsız doğruladı; son hâlde başka değişiklik gerektiren bulgu bildirmedi.

İnceleyenin hedef sonucu **27/27**, tam sonucu **417 testte 415 geçti,
aynı 2 SDK hatası**. Log: `/tmp/atlas-g36-review-final-full-tests.log`.
Bu, yerel bağımsız Codex incelemesidir; ayrı Claude Code incelemesi veya
kullanıcı birleştirme onayı olarak sunulmaz.

## Dış değişiklikler, riskler ve sonraki güvenli adım

- Çalışma sürerken `tp_observation_provenance_candidate.py`,
  `tests/test_tp_observation_provenance_candidate.py` ve bu devir notunun
  başındaki başka Görev 36 kaydı dışarıdan eklendi. Ana ajan ve inceleme
  ajanı bunları oluşturmadı. Bu çalışmada bunlar değiştirilmedi. Kaynak/sahiplik
  için kullanıcıya soru iletildi; cevap alınmadı.
- Son tam keşif 340 başlangıç + bu çalışmanın 27 testi + dış adayın 50 testi
  = 417 içerir. Bu çalışmanın hedef 27 testi dış adaydan bağımsızdır.
- Aktif ortak dosya düzenlemesi nedeniyle AGENTS.md madde 1'deki belirsiz
  işlemi durdurma kuralı uygulandı: **commit bekletildi**. Yeni commit
  kimliği yok; Git index'i değiştirilmedi. Dış değişiklikler sahiplenilmedi.
- Başlangıca göre Owner/STOP/risk/kilit uygulamalarının Git farkı boştur:
  `owner_single_execution.py`, `order_filters.py`, `policy_integration.py`,
  `stop_ambiguity_guard.py`, `emergency_latch_candidate.py`,
  `restart_coverage.py`, `wal_writer_fence_candidate.py`,
  `wal_owner_write_adapter_candidate.py`, `protective_wal_deadline_candidate.py`.
- Canlı API, anahtarlar, Keychain, yasak canlı dizinler ve bot/servisler
  kullanılmadı. Owner ve canlı SDK modülü import edilmedi. Test POST/GET'leri
  yalnız mock; gerçek kilitler yalnız geçici test dizinlerindedir.
- Fence çekişmesinde koruyucu emir gecikmesi tasarım engeli bu görevle
  çözülmez; canlı entegrasyon veya güvenlik kilidi değişikliği yetkisi verilmez.
- Sonraki güvenli adım: dış düzenlemelerin sahibini netleştirerek bu çalışmanın
  dört yeni dosyası ve yalnız bu ek devir kaydını commit'lemek; onaylanan yerel
  SDK kurulumundan sonra tam testleri tekrar çalıştırmak. Dal için GitHub PR
  önerilir; merge yalnız kullanıcıya aittir. Push/merge/PR yapılmadı.
- Gerekli kullanıcı yanıtları: ortak worktree'deki paralel düzenlemenin
  sahipliği ve paket kurulumu. Kritik güvenlik kodu değişikliği yapılmadı;
  böyle bir sonraki iş ayrı onay ve inceleme gerektirir.
- Geri dönüş: bu dört yeni offline dosya ve yalnız bu ek not bölümü ayrı bir
  geri alma değişikliğiyle kaldırılabilir; başka çalışmanın dosyaları ve canlı
  sistem geri dönüş kapsamına girmez. Bu görevde silme veya geri alma yapılmadı.
