# ATLAS FAST — Yalıtılmış geliştirme alanı

Bu depo yalnızca **offline geliştirme** içindir. Mac üzerindeki aktif Owner SDK'ya bağlı değildir; emir gönderilmez ve bu klasördeki yürütücü çalıştırılmaz. Dört temel Python kaynak dosyası, iki offline STOP/yeniden başlatma modülü, SHA-256 manifestleri ve testler bulunur. Binance anahtarı, canlı izin, risk defteri veya emir günlüğü kopyalanmaz.

## İlk geliştirme paketi

- `order_filters.py`: 300.000 ms (5 dakika) exchangeInfo alım zamanı sınırı değiştirilmedi. Eski veri ve gelecekte görünen alım zamanı artık ayrı hata kodlarıyla reddedilir.
- `owner_single_execution.py`: hata kodları mevcut `MAX_RISK=0.75` ve `MAX_LEVERAGE=3` sabitlerinden türetilir; limitler **değiştirilmez**.
- `order_filters.py`: filtre risk ret mesajı geçirilen üst sınırdan üretilir. Belgelerdeki eski 1x/100/0.30 bilgileri mevcut 3x/285/2.70 filtre tavanlarıyla eşleştirildi. Owner yürütücüdeki 0.75 limiti ayrı, daha sıkı bir kapıdır.

Test: `python3 -B -m unittest discover -s tests -v`

## İkinci geliştirme paketi — piyasa verisi gecikmesi

Altı adet geçmiş `EXCHANGE_INFO_STALE_OR_CLOCK_MISMATCH` reddi aynı `run_once` preflight satırında görüldü. Eski loglarda gerçek süre ölçümü bulunmadığından kaynağı kesin olarak belirlenemedi.

- **İlk preflight ve rezervasyon öncesi:** `signed_income_envelope()` önce, `policy_market()` sonra alınır; böylece olası yavaş gelir sorgusu eski market snapshot'ını bekletmez.
- **Fail-closed:** `PolicyGate` yine kaynak güncelliğini, fiyat mark zamanını, gelir belgesi doğruluğunu ve risk bütçesini kontrol eder. Eski bilgiye tolerans, güvenli olmayan yenileme veya yeniden emir denemesi yoktur.
- **Teşhis:** Ret halinde millisecond ölçüleri (`INCOME`, `MARKET`, `INFO_AGE`, `MARK_AGE`) hata mesajına eklenir. `EXCHANGE_INFO_TOO_OLD` ve saat uyuşmazlığı hataları tam yaş/fark değerini bildirir.
- **Sınır:** Bu simülasyon, Binance SDK'sının gerçek ağ gecikmesini ölçmez. Gelir belgesi sorgusunun kendisi hata verirse helper güvenli biçimde başarısız olur; aşama sonu metrikleri üretilemez. Gerçek kök neden için daha sonra salt-okunur entegrasyon testleri gerekir.

Çevrimdışı regresyon testleri: `python3 -B -m unittest discover -s tests -v`.

## Üçüncü geliştirme paketi — onay süresi ve rezervasyon güvenliği

Owner'ın tek kullanımlık onayı için **15 saniyelik** mevcut zaman sınırı değiştirilmedi.

- İkinci piyasa/veri sorgusu öncesi ve sonrasında onay yaşı tekrar ölçülür.
- Risk rezervasyonu başarılı olduktan sonra yeniden kontrol yapılır. Süre aşılmışsa ENTRY yazılmaz ve mevcut `policy_settle_after_flat(..., "0")` prosedürü çağrılır; yeterli borsa kanıtı yoksa rezervasyon bloke kalır.
- `one_write(..., role="ENTRY")` onay zaman damgasını zorunlu tutar. Yürütücünün mutasyon kilidi alındıktan **sonra**, emir günlüğü ve POST öncesinde onay yeniden doğrulanır. Süre aşılırsa `PreSendRefused` çıkar; ne jurnal satırı ne de ENTRY POST'u üretilir.
- STOP/TP emirlerine ENTRY onayı için ek yaş şartı uygulanmaz; mevcut koruyucu emir davranışı korunur.

**Sınır:** Kontrol, mutasyon kilidi alındıktan sonra fakat journal yazmadan önce yapılır. Journal/transport adımları arasında geçen mikro-gecikme için mutlak anlık garanti vermez; canlı entegrasyon onayı ve daha kapsamlı güvenlik testleri olmadan dağıtıma uygun kabul edilmez.

Test: `python3 -B -m unittest discover -s tests -v` (AST ile yalnız işlevler + tamamen sahte venue).

## Dördüncü geliştirme paketi — bağlantı kesintisi, STOP ve yeniden başlatma (offline)

**Yalnızca sentetik testler; canlı SDK import edilmez ve hiçbir gerçek Binance emri gönderilmez.**

Bu paket, Owner SDK'nin `restart_coverage.py` ve `stop_ambiguity_guard.py` dosyalarının salt-okunur kaynak kopyalarını ekler. Kaynak SHA-256 değerleri `GUARD_SOURCE_SNAPSHOT.json` içindedir; günlük, API anahtarı, oturum veya borsa durum dosyası kopyalanmadı.

- `tests/test_order_failure_injection.py`: AST ile yalnız `one_write`, `place_stop_write` ve yeniden başlatma için gerekli fonksiyonları çalıştırır. Ağ bağlantısı kopmuş gibi davranan sahte `post_once` sonucunda **ENTRY/STOP POST tekrarlanmaz**. Eksik imzalı okuma en fazla altı GET simülasyonu üretir; yeniden POST yapmaz. STOP'da yalnızca gönderim öncesi geçici lease hatasında sınırlı tekrar olur. Hatalı WAL, incident ve aktif STOP manifesti yeni girişe engeldir.
- `tests/test_restart_guard_offline.py`: `restart_coverage` içindeki gerçek koruma tarama ve gözlem penceresi mantığı, sahte pozisyonlar ve alarmlarla sınanır. Korunmamış açık pozisyon, belirsiz STOP, süre aşımı, alarm günlüğü hatası ve WAL okuma hatası yeni girişi kapatır. `FLAT_NO_STOP_SENT` tek başına stop koruması anlamına gelmez; `startup_reconcile` ayrıca WAL çözümünü kontrol etmelidir.
- `tests/test_planned_stop_binding.py`: `stop_ambiguity_guard.classify` gerçekte planlanan STOP'a bağlanır. Yanlış trigger, workingType, yabancı emir veya zaman aşımında `PROTECTED` iddiası verilmez.
- Gözlem penceresi yalnızca **okuma anlarındaki** kanıtları temsil eder; kesintisiz fiziksel koruma garantisi değildir. R2 acil emir kanalı kapalıdır; otomatik düzleştirme denenmedi.
- Sınırlama: Tek başına AST ve modül düzeyindeki testler, canlı Owner SDK'nın tüm bağımlılıklarını veya gerçek ağ davranışını doğrulamaz. Entegre sürüm testi ve ayrı dağıtım incelemesi gereklidir.

Komut: `python3 -B -m unittest discover -s tests -q`.

## Beşinci geliştirme paketi — entegre güvenlik zinciri (offline)

`tests/test_safety_chain_integration.py` birden fazla gerçek Owner kaynak işlevini AST üzerinden **geçici WAL klasörü, sahte risk defteri ve sahte borsa yanıtlarıyla** beraber sınar. Testler süre aşmış onay, gelir okuma kesintisi, belirsiz ENTRY, eksik STOP, FLAT sonrası açık EXIT ve yeniden başlatmanın ancak kapatılmış scope ile serbest kalması durumlarını kapsar.

Önemli operasyonel bulgu: `policy_settle_after_flat(..., "0")` risk defterini başarıyla kapatabilse bile `resolved_<scope>.json` yazmaz; bu nedenle `startup_reconcile` rezervasyonu açık WAL olarak tutup yeni işlemleri engelleyebilir. Güvenli olduğu için bu pakette otomatik çözüm işareti veya dosya temizleme eklenmedi. Kanıt şartları ve ayrıntılı senaryolar: `INTEGRATION_VALIDATION_20261105.md`.

**Uyarı:** Bu testler tüm `run_once` üretim akışını veya gerçek Binance SDK bağlantısını çalıştırmaz. Canlı kullanıma hazır sürüm onayı değildir.

## Altıncı geliştirme paketi — sıfır giriş rezervasyon WAL çözümü (offline)

Beşinci paket, imzalı `CLOSED` defter kaydından sonra `POLICY_RESERVE_<scope>.json` satırının çözümlenmemiş kalabileceğini göstermişti. Altıncı pakette yalnızca **kesin pre-send ret** için `_settle_zero_entry_and_resolve()` adayı eklendi. Binance'in tam clientOrderId için kesin `OrderNotFound` (-2013) yanıtı, `SIGNED_INCOME_REALIZED_PNL` kaynaklı sıfır-PNL defter kapanışı, tam WAL kimliği, hiçbir ek emir günlüğü/incident bulunmaması, defterin yeniden okunması ve **yeniden alınmış imzalı FLAT + tüm açık emirlerin sıfır olması** birlikte kanıtlanırsa yalnızca çözüm işareti oluşturulur. Eski dosya silinmez.

POST sonucu belirsizse, herhangi bir ENTRY günlüğü varsa, GET timeout verirse veya emir bulunursa otomatik WAL çözülmez. 29 yeni test (19 WAL/kanıt zinciri + 10 kesin Binance HTTP hata sınıflandırması) toplamı **101/101** yaptı. Açıklama ve dağıtım sınırlamaları: `ZERO_ENTRY_WAL_RESOLUTION_20261105.md`.

Bu kod yalnızca geliştirme kopyasındadır; gerçek Binance SDK / imzalı GET dönüşümü ve tam yürütücü entegrasyonu henüz ayrıca doğrulanmalıdır.

## Yedinci geliştirme paketi — yüklü Binance SDK sözleşme testleri

`tests/test_sdk_transport_offline.py` mevcut `AtlasFastOwnerSDK/.venv` ortamındaki gerçek USDⓈ-M SDK sürümlerini (`17.6.0`, `binance-common 4.5.1`) ve REST imzalı GET metotlarının Python arayüzünü doğrular. Owner kaynak kodundaki HTTP istek korumasını **mock transport ile**, ağ/kilit/anahtar kullanmadan sınar. Önceki 101 teste 14 kontrol eklenmiştir: **115/115**.

Gerçek Binance üzerinde `query_order` read-only sorgusu `SenderLocked` güvenlik kilidiyle reddedilmiştir. Kilit bypass edilmedi ve canlı SDK nesnesi çalıştırılmadı. Bu nedenle `OrderNotFound` için gerçek Binance yanıtı hâlâ **bekleyen entegrasyon kanıtıdır**.

Teknik rapor: `SDK_OFFLINE_INTEGRATION_20261105.md`.

## Offline sürüm adayı RC1

Yedi paket tek bir **yerel Git sürüm adayı** olarak `atlas-fast-v0.1.0-rc1-offline-20261105` etiketiyle tanımlanır. Belgeler: `RELEASE_CANDIDATE_20261105.md`, `GO_NO_GO_CHECKLIST_20261105.md` ve `RC_SOURCE_HASHES_20261105.json`. Sürümün kararı **NO-GO FOR LIVE** olup GitHub'a gönderim, aktif Owner SDK güncellemesi, canlı Binance emir izni veya risk ayarı değişikliği içermez.

## Sekizinci geliştirme paketi — tam `run_once` ağsız akış testi

`tests/test_run_once_full_flow_offline.py`, gerçek `run_once` fonksiyonunu AST üzerinden izole olarak çalıştırır; tüm dış bağlantılar (telefon grant, live R2, Binance venue, ENTRY/STOP/TP, policy, WAL, ağ, observer) sahte bağımlılıktır. Normal sıralama ve 16 ayrı hata/engelleme yolu olmak üzere **17 yeni senaryo**; toplam **132/132** çevrimdışı test başarılı.

Bu bir **kontrol akışı simülasyonudur**, gerçek giriş emri veya güvenlik kapısı onayı değildir. `atlas-fast-v0.1.0-rc1-offline-20261105` etiketi değişmez, yeni adımlar sadece geliştirme dalındadır. Ayrıntılar: `RUN_ONCE_OFFLINE_FLOW_20261105.md`.

## Dokuzuncu geliştirme paketi — yeniden başlatma ve WAL yarışı

`_restart_entry_fill` artık ENTRY WAL dosyasının yokluğunu emir yokluğu olarak kabul etmez: tam clientOrderId için imzalı kesin `OrderNotFound` kanıtı gerekir. Reserve-only sıfır-fill restart çözümü de ek `_resolve_verified_zero_entry_scope` kanıt kapısından geçer. **9 yeni pozitif/negatif test** eklendi.

**İnceleme bulgusu:** Son WAL okuması ile `resolved` işareti arasında TOCTOU yarışı bulundu. Dokuzuncu paketin ilk testinde bu açık `@unittest.expectedFailure` ile gösterildi. Sonraki güvenli muhafaza değişikliği, ortak kilit tüm yazıcılar için kanıtlanana dek **otomatik sıfır-ENTRY WAL çözümünü devre dışı bırakır** (`ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN`). Bu aşamada 142/142 test normal geçti; test artık yalnızca **çözüm işaretinin yazılmadığını** kanıtlar, yarışın atomik olarak düzeltildiğini değil. Tam ortak kilit tasarımı hâlâ açık; **NO-GO FOR LIVE**.

Teknik rapor: `WAL_RACE_REVIEW_20261105.md`. RC1 etiketi değiştirilmedi.

## Onuncu geliştirme paketi — ortak WAL kilidi izole prototip

`wal_writer_fence_candidate.py`, **yalnızca geliştirme alanında** gerçek Mac/POSIX `flock` kilidiyle WAL yazıcısı ile resolverın yarışını test eder. `tests/test_wal_writer_fence_candidate.py` içinde iki bağımsız süreç yarışması dahil 14 yeni test vardır; toplam **156/156 offline test başarılı**.

Bu modül **henüz Owner yürütücüsündeki bütün WAL yazıcılarına bağlanmadı**. Prototipin `proof=True` girişi yalnızca test amaçlıdır, Binance veya risk defteri kanıtı değildir. Önceki fail-closed koruma korunuyor: otomatik sıfır-ENTRY WAL çözümü kapalı; **NO-GO FOR LIVE**. Ayrıntılar: `WAL_WRITER_FENCE_PROTOTYPE_20261105.md`.

## Güvenlik ve sınırlar

Testler yalnızca seçilmiş yürütücü fonksiyonlarını AST üzerinden ve ayrı kopyalanan STOP/yeniden başlatma modüllerini sentetik borsa yanıtlarıyla çalıştırır. Owner uygulaması, ağ bağlantısı, şifre, imzalı GET veya gerçek POST/DELETE testi yapılmaz. Tüm entegre testler ve canlıya alma ayrı inceleme gerektirir.

Bilinen sözleşme borcu: `order_filters.py` içindeki `margin_1x_usdt` çıktı adı eskidir; değer aslında kaldıraçla bölünmüş marjindir. Olası tüketicileri bozmamak için ilk pakette alan adı değiştirilmedi.

Kaynak dosyalar `SOURCE_SNAPSHOT.json` içindeki SHA-256 özetleri ile Mac'in aktif Owner SDK'sından yalnızca okunarak alınmıştır. Bu dalın GitHub'a gönderimi yapılmamıştır.
