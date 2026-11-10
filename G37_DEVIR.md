# G37 — Offline TP durum izleyicisi ve alarm günlüğü

Tarih: 2026-11-10. Görev: `hknylmzxx-source/atlas-fast-dev`, Issue #2 / G37.
Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g37`.
Dal: `codex/g37-tp-alarm`. Başlangıç HEAD: `349e772593d7332fa1838af6b268610d6dbcae4c`.
Kullanıcı talimatıyla commit/push/merge/PR yapılmadı; G37 commit kimliği yok.

Eklenen dosyalar:

- `tp_lifecycle_offline_g37.py`: stdlib ile standalone izleyici, değişmez olaylar,
  diskte append-only günlük, kalıcı yerel alarm kuyruğu ve sentetik CLI demosu.
- `tests/test_tp_lifecycle_offline_g37.py`: 47 unittest; yalnız mock/geçici dizin.
- `G37_DEVIR.md`: bu kaynak, test ve sınır özeti.

Mock kaynak nesnesinin kimliği, aynı makbuz nesnesi ve üretim anındaki alan
kopyası birlikte kontrol edilir. Role/scope/symbol/order-id/qty, durum, dolum,
zaman ve okuma sırası makbuza bağlıdır. `signed=True`, kopya makbuz veya başka
kaynak nesnesi kanıt sayılmaz. Gerçek imza/provenance doğrulaması YOKTUR.

Her TP için en fazla bir sentetik POST niyeti kaydedilir. Belirsiz sonuç
`UNKNOWN_NO_RETRY` ve `OWNER_REVIEW` üretir. TP1 `NEW → EXPIRED` owner alarmı;
TP2 `NEW → PARTIAL` reconcile alarmı üretir. Timeout dolum/sıra bilgisini
korur; sonraki geçerli mock okuma yalnız telemetriyi uzlaştırabilir. Alarm
geçmişi dolumla silinmez. Geçersiz, mükerrer, eski, gelecek tarihli, sırasız
ve çelişkili gözlem ilgili TP'yi owner review durumunda karantinaya alır.
Diğer TP izlenebilir. Disk replay yeni niyetleri `NO_NEW_ENTRY` ile reddeder.
Hiçbir durumda giriş yetkisi verilmez; emir/cancel/owner kilidi temizleme ve
STOP entegrasyonu bulunmaz. Alarmlar aynı olay içindeki yerel outbox'tır;
harici owner servisine bildirim teslim edildiği iddia edilmez.

Günlük: sabit alan/değer allowlist'i; ham yanıt/header/exception/credentials
kaydı yok. Sentetik kimlik alanları caller tarafından sağlanır; bu alanlara
gizli veri konulmamalıdır. UUID, sıra ve SHA256 zinciri; `O_EXCL` geçici dosya,
dosya fsync, ezmeyen atomik hard-link yayını ve dizin fsync kullanılır.
flock ve beklenen kuyruk özeti eşzamanlı/eski yazarları reddeder. Yarım yazım,
pending dosyası, bozuk kayıt, symlink veya I/O hatası kapalı kalır; otomatik
onarım/silme/retry yoktur. Dosya ve her iki dizin fsync hata aşaması testlidir.

Çalıştırma (varsayılan demo geçici dizin kullanır):

```sh
python3 -B tp_lifecycle_offline_g37.py
python3 -B -m unittest discover -s tests -p test_tp_lifecycle_offline_g37.py -v
python3 -B -m unittest discover -s tests -v
```

| Doğrulama | Toplam | Geçen | Hata |
| --- | ---: | ---: | ---: |
| Değişiklik öncesi tam küme | 340 | 338 | 2 |
| Son hedefli G37 koşusu | 47 | 47 | 0 |
| Son tam küme | 387 | 385 | 2 |

İki mevcut hata `test_sdk_transport_offline.InstalledBinanceSdkContracts`
altındaki `test_actual_rest_class_offers_signed_gets` (ModuleNotFoundError)
ve `test_exact_sdk_dependency_versions` (PackageNotFoundError) testleridir.
Eksik paket: `binance-sdk-derivatives-trading-usds-futures`. Skip/gizleme,
paket kurulumu veya ağ erişimi yapılmadı. Tam küme başarılı DEĞİLDİR.
Bağımsız CLI exit=0: TP1 EXPIRED, TP2 FILLED, 2 sentetik niyet, 4 alarm;
replay sonrası 11 olay, aynı alarmlar, `entry_authorized=false`.
Testler ikinci kalıcı CLI koşusunu ve bağımsız süreçlerde yazıcı yarışını da doğrular.

Sınırlar/riskler: POSIX yerel dosya sistemi ve güvenilen Python süreci
varsayılır. Frozen nesneler/API append-only davranışı OS seviyesinde WORM
değildir. SHA256 imza değildir; disk sahibinin tüm günlüğü yeniden yazmasını
veya geçerli kuyruk sonunu silmesini harici kök olmadan kanıtlayamaz. Günlük
her yazımda baştan doğrulanır; yüksek hacim/rotasyon bu görevin kapsamında
değildir. Replay aynı yaş politikasını kullanır; daha sıkı politika eski
kayıtları reddedebilir. Gerçek borsa/owner bağlantısı veya canlı güvence yoktur.

G36 worktree'sine erişilmedi; G36 kodu varsayılmadı. Mevcut kaynak/testler,
kritik modüller ve `DEVIR_NOTU.md` değiştirilmedi. Silinecek eski kod yok.
Sonraki güvenli adım: en son Claude'un bu üç yeni dosyayı bağımsız incelemesi
ve testleri kendisinin çalıştırmasıdır; Claude incelemesi henüz yapılmadı.
Bu offline çalışma için bekleyen kullanıcı onayı yok; birleştirme yapılmadı.
