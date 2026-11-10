# G40 / Issue #5 — F-00 çevrimdışı test istikrarı ve SDK ön hazırlığı

Tarih: 10 Kasım 2026. Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g40`.
Dal: `codex/g40-test-stability`.
Başlangıç ve mevcut HEAD: `2ed4133dd4be3a84b65834701b5c2584db40de66` (G39).
Yeni commit oluşturulmadı; commit/push/merge/PR yapılmadı. Claude incelemesi
kullanıcının istediği son aşama olarak bekliyor.

## Sonuç ve sınırlar

**Tam paket yeşil değil: son koşu 491 test, 489 başarılı, 2 SDK hatası,
0 assertion başarısızlığı, 0 atlama; çıkış 1.** Önceki 486 testlik pakete
yalnız beş yeni G40 testi eklendi. Mevcut flaky test düzeltilmedi veya
gevşetilmedi; sorunun oluşmasına yeterli iki koşul ayrı ayrı gösterildi.

- Eski gerçek saat dosyası beş bağımsız doğal koşuda 25/25 geçti. Bu sonuç
  düşük sıklıktaki yarışı dışlamaz.
- Ebeveynin `LOCKED` sonrasında ancak çocuk kilidi bıraktığında devam etmesi
  kontrollü olarak sağlandı. Eski hedef test, aynı assertion ile **5/5
  başarısız** oldu: `ProtectiveJournalDeadlineExceeded not raised`.
- Enjekte edilen monotonic saat ve sleep ile deadline sonrasındaki yeniden
  denemede kilit boşsa WAL yazıldığı; meşgulse timeout verildiği gösterildi.
- Yeni, bırakma mesajına bağlı fixture ile timeout sonrasında kilit
  bırakıldığında kendiliğinden yeniden deneme/yazma oluşmadığı doğrulandı.
- İki karakterizasyon testinin geçmesi mevcut davranışın teşhis edildiği
  anlamına gelir; deadline güvenliğinin sağlandığı anlamına gelmez.

Yalnız bu worktree'de çalışıldı. Ağ, GitHub/Issue içeriği, başka worktree,
canlı Binance, owner SDK, `/Users/Shared/Atlas`, `~/Atlas/AKTIF_*`,
`~/ATLAS_SAHIP`, Keychain ve credentials kullanılmadı. Python testleri yerel
geçici dizinler ve sentetik süreçlerle çalıştı. Yeni paket, `.venv`, trade auth,
servis değişikliği veya emir işlemi yok. Owner kaynak dosyası yalnız metin/AST
olarak incelendi; modül olarak import edilmedi.

## Yarışın teşhisi

Kaynaklar:
`tests/test_protective_real_clock_fence_offline.py`,
`protective_wal_deadline_candidate.py`, `wal_writer_fence_candidate.py`.

### 1. Readiness mesajı kilidin kalan ömrünü garanti etmiyor

Eski çocuk, gerçek `writer_fence` aldıktan sonra `LOCKED` yazar ve 150 ms
uyur. Ebeveyn `readline()` ile bu mesajı bekler, ardından 25 ms timeout ile
append çağırır. Mesaj, kilidin geçmişte alındığını gösterir; ebeveynin çağrıya
başladığı anda hâlâ tutulduğunun kanıtı değildir.

Olası sıra:

```text
çocuk:   kilit al -> LOCKED -> 150 ms uyku -> kilit bırak
ebeveyn: LOCKED oku -> scheduler gecikmesi -----------> append başlat
                                                       ilk yazma başarılı
```

Deadline çocuğun `LOCKED` anından değil, `append_protective_bounded` çağrısının
içinde başlatılır. Ebeveyn 150 ms veya daha uzun süre schedule edilmezse
25 ms'lik bütçe başladığında kilit zaten boş olabilir. Böyle bir durumda
exception bekleyen assertion yanlış fixture önkoşuluna dayanır.

Kontrollü deney, yalnız çalışan test örneğinin `child` metodunu sarmaladı:
orijinal child çağrısından sonra `proc.wait(timeout=5)` ile kilidin bırakılması
beklendi ve aynı test metodu çalıştırıldı. Kaynak/test dosyası değiştirilmedi;
fixture'ın 150 ms süresi veya hedef assertion gevşetilmedi. Beş denemenin
beşinde aynı assertion başarısız oldu; deney komutu çıkış 1 verdi.
Bu, scheduler gecikmesinin yeterli bir açıklama olduğunu kanıtlar; G39
incelemesindeki tek doğal hatanın zaman çizelgesi kaydedilmediği için o olayın
kesin kök nedeni olduğu iddia edilmiyor.

### 2. Monotonic deadline yalnız meşgul kilit hatasından sonra kontrol ediliyor

Mevcut döngü önce `append_order_journal` dener. Yalnız `WAL_FENCE_BUSY` gelirse
`deadline - clock()` hesaplanır. Uyku sonrasında yeniden yazmayı denemeden önce
deadline kontrolü yoktur. Bu nedenle istenen 5 ms'lik uyku scheduler yüzünden
uzarsa ve çocuk arada kilidi bırakırsa deadline sonrasındaki deneme başarılı
olabilir.

Yeni testte gerçek yerel kilit ve WAL kullanıldı; yalnız saat ve sleep
enjekte edildi. Yerel journal çağrısı `mock.patch` ile sarılarak deneme
zamanları kaydedildi; bir SDK/transport sahteciliği yapılmadı:

| Adım | Enjekte monotonic zaman | Sonuç |
| --- | --- | --- |
| Deadline başlangıcı | 100.000 | Deadline 100.025 |
| İlk append | 100.000 | Gerçek çocuk kilidi: `WAL_FENCE_BUSY` |
| İstenen sleep | 0.005 saniye | Test bırakma mesajını gönderir, çocuk çıkışı doğrulanır |
| Scheduler oversleep karşılığı | 100.150 | Deadline 125 ms önce dolmuş |
| İkinci append | 100.150 | Kilit boş; `STOP_G40_OFFLINE.json` yazılır |

Aynı 150 ms saat ilerlemesinde çocuk kilidi bırakmazsa ikinci deneme meşgul
kalır, `ProtectiveJournalDeadlineExceeded` oluşur ve marker yazılmaz.
Bu fark, deadline kontrolünün yalnız hata yolunda kaldığını gösterir.
Enjekte zaman, işletim sisteminde ölçülmüş bir scheduler gecikmesi değildir.

Yerel yorumlayıcı Python `3.13.15`:
`/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`.
`time.get_clock_info('monotonic')` çıktısı `mach_absolute_time()`,
`monotonic=True`, `adjustable=False`, çözünürlük yaklaşık `4.17e-8` saniye.
Duvar saati ayarlaması temel açıklama değildir. Monotonic saat, sürecin
zamanında çalışmasını veya `sleep` çağrısının tam istenen sürede dönmesini
garanti etmez. CPU yükü/preemption, pipe bildirimi sonrasındaki gecikme ve
dosya sistemi işlemleri bu pencereyi büyütebilir; sistem yükü ayrıca ölçülmedi.

### 3. Temp-dir, mock ve temizlik değerlendirmesi

Eski fixture her testte ayrı `TemporaryDirectory` açar; çocuk ve ebeveyn aynı
dizinin gerçek POSIX advisory lock'unu kullanır. Çocuk ACK'yi kilit alındıktan
sonra verir. Kaynak incelemesi ve deneylerde dizin çakışması, yanlış dizin veya
önceki koşudan kalan marker kanıtı bulunmadı. `LOCKED` öncesindeki süreç
başlama yarışı readiness ile karşılanıyor; mesajdan sonraki kalan kilit ömrü
karşılanmıyor. Eski `stdout.readline()` bekleyişinin ayrıca üst sınırı yok.

Yeni G40 fixture standart `TemporaryDirectory` tarafından özel izinlerle
oluşturulan dizini kullanır; izin değiştirme komutu eklenmedi. Readiness,
`selectors` ve monotonic son süreyle 5 saniyeye sınırlıdır; EOF ve bozuk ACK
hata üretir. `_held_child`, ACK sonrasında gerçek fence'in meşgul olduğunu da
doğrular. Çocuk zaman bazlı normal bırakma yerine `RELEASE` bekler. 10 saniyelik
watchdog, mesaj gelmezse çocuğu hatayla bitirir; sessiz başarı sağlamaz.
Normal bırakmada `RELEASED` ve sıfır çıkış kodu doğrulanır. Temizlik yalnız
testin başlattığı çocukla sınırlıdır; çocuk toparlandıktan sonra temp-dir silinir.
Uzun scheduler duruşları fixture hatası üretebilir; timeout/skip ile örtülmez.

Mock yalnız yerel journal çağrısının gözlenmesi ve readiness yokken hiç
çağrılmadığının doğrulanması için kullanıldı. Readiness olmadan çıkan çocuk
senaryosunda append çağrılmadığı ve marker bulunmadığı doğrulandı.

## Eski dosyalar için uygulanmamış öneri

G37/G38/G39 kaynak/testleri, mevcut flaky test, deadline adayı ve kritik
owner/STOP/risk/kilit dosyalarının hiçbiri değiştirilmedi.

Sonraki ayrı görevde, Claude incelemesi ve gerekli kullanıcı onayıyla:

1. Hedef eski testin çocuk fixture'ı açık bırakma mesajına bağlanmalı.
   Readiness bekleyişi sınırlanmalı; kilit tutulduğu doğrulanmalı.
2. Mevcut 25 ms timeout ve aynı exception assertion'ı korunmalı. Çocuk timeout
   gözlenmeden serbest bırakılmamalı. Önce marker yokluğu doğrulanmalı; sonra
   bırakma ACK'si/çocuk çıkışı ve tekrar marker yokluğu doğrulanmalı.
   Yeni G40 `test_held_child_timeout_then_release_never_retries_or_writes`
   bunun çalışır çevrimdışı örneğidir. İkinci bir append çağrısı eklenmemeli.
3. Doğal zamanlama deneyleri ve deadline sözleşmesi ayrı değerlendirilmelidir.
   Sabit 150 ms'yi büyütmek, timeout'u gevşetmek, başarısız testi yeniden
   çalıştırıp yalnız başarılı sonucu raporlamak veya skip çözüm değildir.
4. Deadline'ın yalnız kilit bekleme bütçesi mi yoksa yazmaya son başlama anı mı
   olduğu açıkça kararlaştırılmalı. Her yeniden denemeden önce kontrol, bazı
   geç yazmaları önler; kontrol ile kilit alma/yazma arasındaki yeni preemption
   penceresini tek başına kapatmaz. Sıkı yazma sınırı gerekiyorsa fence altında
   mutasyondan önce doğrulama tasarlanmalıdır. Bu, burada uygulanmamış ayrı
   güvenlik incelemesidir; fsync tamamlanması için gerçek zaman garantisi yoktur.
   `timeout_s=0` mevcut anında deneme semantiği ayrıca korunmalı/kararlaştırılmalı.
5. Böyle bir düzeltme olursa iki G40 karakterizasyon testi de yeni sözleşmeye
   göre açık regresyon beklentilerine dönüştürülmeli; mevcut geç yazmayı
   başarı olarak korumamalıdır. Kritik kod değişiklikleri bu görevin dışında.

## Test kayıtları

Tüm komutlar worktree kökünde `python3 -B` ile, sıralı çalıştırıldı. Tekrar
sayısı baştan beşle sınırlandı; yük/stress döngüsü veya bağımlılık kurulumuyla
sonuç arama yapılmadı.

| Koşu | Toplam / başarılı | Başarısızlık / hata / atlama | Süre | Çıkış |
| --- | --- | --- | --- | --- |
| Başlangıç eski gerçek saat dosyası, 1–5 | Her biri 5 / 5 | Her biri 0 / 0 / 0 | 1.717, 1.609, 1.575, 1.610, 1.909 sn | Her biri 0 |
| Başlangıç SDK dosyası | 14 / 12 | 0 / 2 / 0 | 0.035 sn | 1 |
| Başlangıç tam paket | 486 / 484 | 0 / 2 / 0 | 12.636 sn | 1 |
| Kontrollü gecikmeyle eski hedef, 1–5 | Her biri 1 / 0 | Her biri 1 / 0 / 0 | Süre ayrıca kaydedilmedi | Toplayıcı 1 |
| Yeni G40 dosyasının ilk geliştirme koşusu | 5 / 2 | 0 / 3 / 0 | 0.872 sn | 1 |
| Yeni G40 dosyası, kaçış düzeltmesi sonrası | 5 / 5 | 0 / 0 / 0 | 1.335 sn | 0 |
| Yeni G40 dosyası, kontrollü tekrar 1–5 | Her biri 5 / 5 | Her biri 0 / 0 / 0 | 1.120, 1.195, 1.395, 0.939, 1.143 sn | Her biri 0 |
| Mevcut deadline testleri | 8 / 8 | 0 / 0 / 0 | 0.009 sn | 0 |
| Mevcut cross-process testleri | 5 / 5 | 0 / 0 / 0 | 0.582 sn | 0 |
| G40 sonrası tam paket | **491 / 489** | **0 / 2 / 0** | 13.450 sn | **1** |

İlk G40 geliştirme hatası: iç içe çocuk kodu dizesindeki `\n` kaçışı gerçek
satır sonuna dönüşerek çocukta syntax hatası yarattı. Üç test
`G40_FIXTURE_READINESS_EOF` verdi. Yalnız yeni dosyada çocuk kodu raw string
yapıldı; takip eden hedefli ve tam koşular yukarıdadır. Hatalı koşu rapordan
çıkarılmadı.

Tekrar edilebilir standart komutlar:

```sh
python3 -B -m unittest discover -s tests -p test_protective_real_clock_fence_offline.py -v
python3 -B -m unittest discover -s tests -p test_clock_fixture_offline_g40.py -v
python3 -B -m unittest discover -s tests -p test_protective_wal_deadline_candidate.py -v
python3 -B -m unittest discover -s tests -p test_protective_cross_process_fence_offline.py -v
python3 -B -m unittest discover -s tests -p test_sdk_transport_offline.py -v
python3 -B -m unittest discover -s tests -v
```

Eski assertion'ı aynı kontrollü ebeveyn gecikmesiyle yeniden üretme (bilerek
**başarısız** test raporlar, dosya değiştirmez, en fazla beş tekrar):

```sh
python3 -B - <<'PY'
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path.cwd() / 'tests'))
import test_protective_real_clock_fence_offline as legacy
name = 'test_no_implicit_retry_when_child_releases_after_timeout'
failed = False
for repeat in range(5):
    case = legacy.CrossProcessDeadlineTests(name)
    original = case.child
    def delayed_child(hold_seconds, child=original, current=case):
        proc = child(hold_seconds)
        current.assertEqual(proc.wait(timeout=5), 0)
        return proc
    case.child = delayed_child
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
    failed = failed or not result.wasSuccessful()
raise SystemExit(1 if failed else 0)
PY
```

## SDK gereksinimleri: yalnız yerel kaynak ve metadata

Yetkili pin kaynağı `owner_single_execution.py:77–78`, `SDK_PINS` sabitidir;
AST ile okundu. SDK testinin sürüm kontrolü bu sabiti kullanır:

| Paket | Tam sürüm | Kontrol kodu | Mevcut metadata/import spec |
| --- | --- | --- | --- |
| `binance-sdk-derivatives-trading-usds-futures` | `17.6.0` | `SDK_VERSION_NOT_PINNED` | İkisi de yok |
| `binance-common` | `4.5.1` | `SDK_COMMON_VERSION_NOT_PINNED` | İkisi de yok |

`importlib.metadata.distribution` her paket için `PackageNotFoundError`
üretti; üst düzey `importlib.util.find_spec` her iki modül için `None` verdi.
SDK modülleri bu metadata incelemesinde import edilmedi. `.venv` mevcut değil;
`sys.prefix == sys.base_prefix`. Başka ortam veya paket cache'i taranmadı.
Geçişli bağımlılık listesi, `Requires-Python`, wheel uyumu ve bunların tam
sürümleri yerel eksik metadata'dan çıkarılamıyor. İki doğrudan pin, tam bir
geçişli bağımlılık kilidi değildir; Python 3.13 uyumu doğrulanmış sayılmaz.

`tests/test_sdk_transport_offline.py` toplam 14 test içerir:

- 12 `OwnerTransportContractTests` testi, seçili Owner guard fonksiyonlarını
  AST'den derler ve yerel fake transport ile çalışır. Hepsi geçti.
- `test_exact_sdk_dependency_versions`, metadata sürümlerini iki pine eşit
  bekler. Döngü ilk eksik pakette durur: USD-M dağıtımı için
  `PackageNotFoundError`. Ek metadata kontrolü `binance-common` eksikliğini de
  doğruladı; bu paket sürüm uyumsuzluğu olarak raporlanmadı.
- `test_actual_rest_class_offers_signed_gets`, şu sınıfı import edemedi:
  `binance_sdk_derivatives_trading_usds_futures.rest_api.rest_api.DerivativesTradingUsdsFuturesRestAPI`.
  Hata `ModuleNotFoundError`; imza assertion'larına ulaşılmadı.
  Beklenen callable metotlar `query_order`, `account_information_v3`,
  `current_all_open_orders`, `current_all_algo_open_orders`.
  `query_order` parametreleri `symbol`, `orig_client_order_id`, `recv_window`;
  `account_information_v3` parametrelerinden biri `recv_window` olmalıdır.

SDK sınıfından örnek oluşturmak veya bu metotları çağırmak test gereği değildir.
Kaynakta görünen HTTPS adresleri fake transport girdisidir; gerçek HTTP isteği
yapılmadı. Gerçek metadata yerine sahte paket/metadata eklenmedi.

## Kullanıcı onayına hazır, ağsız ve izole kurulum planı

**Aşağıdaki kurulum ve geri dönüş komutları çalıştırılmadı.** Bu görevde paket
kurulumu ve ağ yasaktır. Plan yalnız sonradan açık kullanıcı onayı verilirse,
bu worktree'de ve Claude incelemesi sonrasında uygulanabilir.

Gerekli girdiler: kullanıcının onayladığı yerel
`g40_sdk_offline/wheelhouse/` ve `g40_sdk_offline/requirements.lock`.
Bu girdiler oluşturulmadı/temin edilmedi. Lock dosyası yukarıdaki iki pini ve
**tüm zorunlu geçişli bağımlılıkları tam sürüm ve doğrulanmış SHA-256 hash** ile
içermeli; wheel metadata'sı üzerinden bağımsız incelenmelidir. Doğrudan URL,
VCS, editable, ek index/find-links, başka requirements dosyası veya kaynak
paket/build adımı içermemelidir. Wheelhouse Python 3.13/macOS için uyumlu
wheel'leri içermelidir. Eksik/uyumsuz girdi halinde işlem durur; çevrimiçi
indirme, sdist derleme veya pin değiştirme yapılmaz. Hash'in tek başına
paketin güvenilir kaynaktan geldiğini kanıtlamadığı dikkate alınır.

Onay kapsamı yalnız `.venv` oluşturulması, incelenen yerel wheel'lerin
kurulması ve çevrimdışı testlerdir. Sistem Python'una kurulum, pip yükseltme,
`--system-site-packages`, başka worktree/SDK kopyalama veya credentials yoktur.
Komutlar tek tek yürütülür; sıfır dışı çıkışta sonraki adıma geçilmez.

```sh
cd /Users/hakanyilmaz/Atlas/FAST_DEV_codex_g40
python3 -I -B - <<'PY'
from pathlib import Path
import sys
root = Path('/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g40')
assert Path.cwd().resolve() == root.resolve()
assert sys.prefix == sys.base_prefix
assert not (root / '.venv').exists() and not (root / '.venv').is_symlink()
assert (root / 'g40_sdk_offline/wheelhouse').is_dir()
assert (root / 'g40_sdk_offline/requirements.lock').is_file()
print(sys.executable, sys.version.split()[0])
PY
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -I -m venv .venv
./.venv/bin/python -I -m pip --isolated --disable-pip-version-check --no-cache-dir install --no-index --no-deps --only-binary=:all: --find-links ./g40_sdk_offline/wheelhouse --require-hashes -r ./g40_sdk_offline/requirements.lock
./.venv/bin/python -I -m pip --isolated --disable-pip-version-check check
./.venv/bin/python -I -B - <<'PY'
import ast
from importlib.metadata import version
from pathlib import Path
import sys
assert sys.prefix != sys.base_prefix
tree = ast.parse(Path('owner_single_execution.py').read_text())
pins = [ast.literal_eval(n.value) for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'SDK_PINS' for t in n.targets)]
assert len(pins) == 1
for package, pin, code in pins[0]:
    actual = version(package)
    print(package, actual)
    assert actual == pin, code
print(sys.executable)
PY
./.venv/bin/python -B -m unittest discover -s tests -p test_sdk_transport_offline.py -v
./.venv/bin/python -B -m unittest discover -s tests -p test_clock_fixture_offline_g40.py -v
./.venv/bin/python -B -m unittest discover -s tests -p test_protective_real_clock_fence_offline.py -v
./.venv/bin/python -B -m unittest discover -s tests -v
./.venv/bin/python -I -m pip --isolated --disable-pip-version-check freeze --all > .venv/g40-kurulu-surumler.txt
```

`--no-deps`, wheel metadata'sından otomatik bağımlılık indirilmesini önler;
bu yüzden lock tüm bağımlılık kapanımını içermeli ve `pip check` geçmelidir.
`--no-index` ve yalnız incelenmiş yerel wheel yolları kullanılır. Mevcut ağ
sandbox'ı korunur. Kurulumda paket kodu/SDK istemcisi ayrıca çalıştırılmaz;
SDK'nin import davranışı da son testlerden önce Claude tarafından incelenir.
Testler yalnız sınıf/imza incelemesi yapar; canlı SDK/owner çağrısı, sahte veya
gerçek trade yetkilendirmesi açılmaz. Yerel girdiler ve `.venv` Git'e eklenmez.

Kabul ölçütü: pinler birebir aynı, `pip check` başarılı, SDK dosyası 14/14,
G40 dosyası 5/5 ve o andaki tam paket tümüyle başarılı, 0 hata/başarısızlık/
atlama. Şimdiki ağaçta toplam 491 testtir. Kurulum yapılması flaky testin
düzeldiğinin kanıtı değildir; tekrar koşularındaki her sonuç ayrıca raporlanır.

### Geri dönüş

Aktivasyon kullanılmadığından sistem Python'u değişmez. Kurulum/test hatasında
yerel `.venv` kullanımı bırakılır; test alt süreçleri bittikten sonra yalnız
bu dizin aynı worktree'deki karantina konumuna taşınabilir. Toplu silme veya
başka ortama müdahale yoktur; hedef varsa işlem durur:

```sh
python3 -I -B - <<'PY'
from pathlib import Path
import sys
root = Path('/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g40')
assert Path.cwd().resolve() == root.resolve()
assert sys.prefix == sys.base_prefix
source = root / '.venv'
retired_parent = root / '.g40-geri-donus'
target = retired_parent / '.venv'
assert source.is_dir() and not source.is_symlink()
assert not retired_parent.is_symlink()
assert not target.exists() and not target.is_symlink()
retired_parent.mkdir(exist_ok=True)
source.rename(target)
print('Yerel venv karantinaya alındı:', target)
print('Temel yorumlayıcı:', sys.executable)
PY
python3 -B -m unittest discover -s tests -v
```

`.gitignore` içindeki `.venv/`, karantinadaki `.g40-geri-donus/.venv/` için de
eşleşir (`git check-ignore` ile doğrulandı). Karantinadaki ortam çalıştırılmaz;
silinmez. Sistem Python'uyla yeniden koşunun SDK eksikliğine dönmesi beklenir;
bu, yeşil test sonucu olarak sunulmaz. Kaynak dosya değişikliği geri dönüşü
gerekmez; kurulum planı kaynakları değiştirmez.

## Değişiklikler, açık işler ve devir

- Yeni dosya: `tests/test_clock_fixture_offline_g40.py` — beş çevrimdışı test
  ve yalnız testte kullanılan sınırlı süreç fixture'ı.
- Yeni dosya: `G40_DEVIR.md` — bu teşhis, test kayıtları ve uygulanmamış SDK planı.
- Güncellenen dosya: `DEVIR_NOTU.md` — önceki kaydı koruyan kısa G40 bağlantısı.
- Dal/kimlik: `codex/g40-test-stability`, HEAD
  `2ed4133dd4be3a84b65834701b5c2584db40de66`; tüm G40 değişiklikleri commitsiz.
- Kullanılmayan modül tespiti/silme bu işin kapsamı dışında; hiçbir modül silinmedi.
- Açık: eski gerçek saat testinin zamanlama yarışı ve deadline sonrası başarılı
  retry davranışı hâlâ var. Mevcut iki SDK hatası devam ediyor. Tam paket kırmızı.
- Sonraki güvenli adım: Claude'un yeni dosyayı, bu bulguları ve aynı hedefli/tam
  testleri bağımsız incelemesi. Claude bu oturumda çalıştırılmadı.
- Gereken ayrı kullanıcı onayları: incelenmiş yerel SDK girdileriyle `.venv`
  kurulumu; ileride eski test/koruma davranışı değişikliği için ayrı görev ve
  güvenlik incelemesi. Bu görevde kurulum onayı istenip işlem başlatılmadı.
- Kullanıcının talimatı gereği commit/push/merge/PR yok; son inceleme bekliyor.
- Son statik kontrol: değişiklik kapsamı yalnız yukarıdaki üç dosya;
  `git diff --check`, yeni Python/çocuk kodu derlemesi ve belgelerdeki komutların
  zsh sözdizimi başarılı. Kurulum/geri dönüş komutları çalıştırılmadı.
