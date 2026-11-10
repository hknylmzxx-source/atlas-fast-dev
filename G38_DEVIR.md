# G38 — Offline/mock owner kilit preflight

- Görev: `hknylmzxx-source/atlas-fast-dev`, Issue #3 G38; yalnız görev tanımı,
  GitHub'a veya canlı sisteme bağlantı kurulmadı.
- Tarih: 2026-11-10.
- Worktree: `/Users/hakanyilmaz/Atlas/FAST_DEV_codex_g38`.
- Dal: `codex/g38-lock-preflight`.
- Taban commit: `349e772593d7332fa1838af6b268610d6dbcae4c`.
- Yeni commit: yok; kullanıcı talimatıyla commit, push, merge ve PR yapılmadı.
- Durum: uygulama ve hedefli doğrulama hazır. Tam paket yeşil değil;
  aşağıdaki iki SDK bağımlılık hatası ve son Claude incelemesi açık.

## Eklenen üç dosya

1. `lock_evidence_preflight_offline_g38.py`: bağımsız standart kütüphane modülü,
   karar API'si ve 16 sentetik senaryolu CLI.
2. `tests/test_lock_evidence_preflight_offline_g38.py`: 47 hedefli unittest;
   alt senaryolarla kaynak, değer, zaman ve çapraz referans boyutları.
3. `G38_DEVIR.md`: bu devir notu. Kullanıcının üç dosya sınırı nedeniyle
   mevcut `DEVIR_NOTU.md` değiştirilmedi.

## Karar sözleşmesi

Her sonuç, olumlu fixture dahil, `NO_UNLOCK` ve `NO_NEW_ENTRY` içerir.
`manual_owner_review_required` daima `true`; mod `OFFLINE_MOCK_ONLY`.

- Uyumlu mock kanıt: `MANUAL_OWNER_REVIEW`,
  `EVIDENCE_COHERENT_FOR_MANUAL_REVIEW`.
- Eksik, belirsiz veya uygunsuz kanıt: `FAIL_CLOSED`, ilgili neden kodları.
- Bütün sonuçlarda `MOCK_PROVENANCE_ONLY` ve
  `MANUAL_OWNER_REVIEW_REQUIRED` bulunur.
- Sonuç oluşturucusunda izin alanları değiştirilemez; sonuç nesnesi frozen'dır.
  Bu Python nesneleri düşmanca Python koduna karşı bir güvenlik sınırı değildir.

### Kaynak ve receipt modeli

`OfflinePreflight`, dışarıdan kanıtlarla birlikte gönderilen rastgele kaynağa
güvenmez; önceden yapılandırılmış, ayrı kimlik ve adlara sahip üç `MockSource`
nesnesini kullanır. Her kaynak yalnız bir okuma türünü temsil eder:

- `position`: brüt açık pozisyon miktarı sıfır; ters yönlü pozisyonların
  netleştirilmiş toplamı modellenmez. Tam sayı veya sonlu `Decimal` kabul edilir.
- `normal_open_orders`: normal açık emir sayısı sıfır; yalnız tam sayı.
- `conditional_algo_open_orders`: conditional/algo açık emir sayısı sıfır;
  yalnız tam sayı.

`MockSource.issue()` makbuzun nesne kimliğini ve o anda üretilen içerik
damgasını bellekte kaydeder. Aynı alanları taşıyan elle oluşturulmuş nesne,
`dataclasses.replace`, sığ/derin kopya, aynı adlı başka kaynağın makbuzu ve
sonradan değiştirilmiş özgün makbuz güvenilir kabul edilmez. Kaynak alt sınıfı
ile doğrulama atlatma da reddedilir. Bu gerçek imza/kriptografi değildir;
verinin gerçek bir hesaptan geldiğini veya doğru olduğunu kanıtlamaz.

`signed_owner_reads=True` / `signed_flat_confirmed=True` içeren dict veya
nesneler kanıt sayılmaz. `False == 0` Python eşitliği sıfır kanıtı değildir:
bool, string, None ve sonlu olmayan pozisyon değerleri reddedilir.

Her makbuzun scope/account/symbol alanı beklenen bağla birebir eşleşir.
Zamanlar negatif olmayan tam sayı milisaniyedir. Gelecek zaman reddedilir;
varsayılan yaş sınırı 5.000 ms ve sınır değeri dahildir. `now_ms` ve yaş sınırı
bu offline simülasyonun girdileridir; güvenilir canlı saat iddiası yoktur.
Her tür için tam bir makbuz gerekir; tekrar ve bilinmeyen tür fail closed'dur.

### Ledger / WAL / incident çapraz kontrolü

Mock journal içinde yalnız şu üç kayıt kabul edilir:

| Tür | Gerekli durum |
| --- | --- |
| `ledger_closed` | `CLOSED` |
| `wal` | `CLOSED` |
| `incident` | `SUPHELI` |

Kayıt kimlikleri farklı, dolu ve tekrarsız olmalıdır. Her üç kaydın
`ledger_id`, `wal_id`, `incident_id` alanları bu üç gerçek kayıt kimliğine
karşılıklı bağlanır; her kayıt aynı scope/account/symbol bağındadır.
Yalnız `ledger_closed` bulunması yeterli değildir. Bilinmeyen kayıtlar,
tekrarlar, kayıp kayıtlar, yanlış durum ve kopuk bağlantılar reddedilir.
`SUPHELI` korunur; olay çözülmüş veya temizlenmiş kabul edilmez.

### Crash ve TOCTOU/disk sınırı

`MockJournal` yalnız bellektedir; gerçek kilit, dosya veya kill-switch işlemi
yapmaz. Snapshot kaynak journal nesnesine ve içeriğine bağlıdır; güncel ve
gelecekte olmayan snapshot gerekir. `cleanup_phase` yalnız `INTACT` olabilir;
`PREPARED`, `PARTIAL`, `REMOVED`, `DONE`, bilinmeyen durum ve kayıp kayıtlar
crash/yarım temizlik şüphesiyle fail closed olur. Geri alma veya temizlik
uygulanmaz.

Bellek modeli değişiklik neslini son kontrolde karşılaştırır. Snapshot sonrası
değişim, inceleme sırasında değişim ve eski içeriğe geri dönme (ABA) test edilir.
Son kontrolden sonraki değişimi önleyen atomik bir işlem yoktur. Bu kontrol
gerçek disk TOCTOU güvencesi sayılmaz ve hiçbir yetki üretmez.

Disk güvenliği bu görevde yalnız tasarım sınırıdır: gelecekte bağımsız inceleme
gerektiren bir okuyucu için güvenilir dizin, symlink izlemeyen descriptor
açılışı, düzenli dosya/sahip/izin denetimi, descriptor üzerinden tutarlı okuma,
okuma öncesi/sonrası kimlik-nesil kontrolü ve tüm yazıcılarla ortak tutarlılık
protokolü gerekir. Bu modülde bunların hiçbiri uygulanmış veya doğrulanmış
sayılmaz. Gerçek dosya okuma/yazma yolu ve canlı temizleme API'si yoktur.

## Kullanım

Yalnız bu worktree'de, ağ ve paket kurulumu gerektirmeden:

```sh
python3 -B lock_evidence_preflight_offline_g38.py --scenario coherent
python3 -B lock_evidence_preflight_offline_g38.py --scenario crash-partial
python3 -B lock_evidence_preflight_offline_g38.py --scenario all
```

CLI JSON listesi üretir. `coherent` çıkış kodu 0 yalnız raporun manuel incelemeye
uygunluğunu ifade eder; kilit açma izni değildir. Uygunsuz senaryo çıkış kodu 2;
`all` kasıtlı uygunsuz fixture'lar içerdiği için 2 döner. Dış dosya, hesap,
anahtar, endpoint veya canlı çalışma seçeneği bulunmaz.

Python API örneği:

```python
from lock_evidence_preflight_offline_g38 import build_scenario

decision = build_scenario("coherent").run()
assert decision.unlock == "NO_UNLOCK"
assert decision.new_entry == "NO_NEW_ENTRY"
```

## Testler ve sonuçlar

Önce hedefli test çalıştırıldı:

```sh
python3 -B -m unittest discover -s tests -p test_lock_evidence_preflight_offline_g38.py -v
```

Sonuç: **47 test / 47 başarılı**, 0 hata, 0 atlama (0,030 s).
Kapsam: olumlu kanıtın bile izin vermemesi; bool taklidi; makbuz kimliği,
kopyalama ve içerik tahrifi; üç ayrı kaynak; her okuma türünde eksik/sıfır dışı,
stale/future ve bağ uyuşmazlığı; bozuk değer/zaman; bilinmeyen/tekrar kayıt;
tüm çapraz referanslar; yarım temizlik; snapshot güveni; TOCTOU/ABA;
CLI JSON/çıkış kodları ve yalnız standart kütüphane importları.

Ardından istenen tam komut çalıştırıldı:

```sh
python3 -B -m unittest discover -s tests -v
```

Sonuç: **387 test / 385 başarılı / 2 hata**, 0 atlama (4,802 s), çıkış kodu 1.
İki hata, görevde bildirilen eksik Binance SDK bağımlılığıyla örtüşür:

1. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_actual_rest_class_offers_signed_gets`:
   `ModuleNotFoundError: No module named 'binance_sdk_derivatives_trading_usds_futures'`.
2. `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions`:
   `PackageNotFoundError: No package metadata was found for binance-sdk-derivatives-trading-usds-futures`.

Hatalar gizlenmedi, testler atlanmadı veya değiştirilmedi; paket yüklenmedi.
Tam stdout/stderr günlüğü:
`/private/tmp/g38-lock-preflight-full-tests-20261110.log`.

Gerçek CLI süreç doğrulaması: `coherent` → çıkış 0 / manuel inceleme;
`crash-partial` → çıkış 2 / fail closed. İkisinde de `NO_UNLOCK`, `NO_NEW_ENTRY`.

## Açık işler ve sonraki güvenli adım

- Tam paket iki mevcut SDK bağımlılık hatası nedeniyle yeşil değildir.
  Görev sınırları içinde bağımlılık kurulumu yapılmayacak.
- Kullanıcının talimatıyla Claude incelemesi en sona bırakıldı ve yapılmadı;
  sonraki adım bu üç yeni dosyanın Claude tarafından bağımsız incelenmesidir.
  İnceleme sonucu olmadan çapraz inceleme tamamlandı iddiası yoktur.
- Gerçek owner kanıtı, kriptografik doğrulama, disk dayanıklılığı veya deploy
  bu çalışmanın çıktısı değildir. Canlı kilit temizleme aracı kullanılmadı.
- Kritik mevcut dosyalar ve diğer çalışma alanları değiştirilmedi; yeni kodda
  ağ, emir gönderimi, owner/live importu veya dosya mutasyonu bulunmaz.
- Mevcut kod silinmedi, bağımlılık/hook/framework eklenmedi.
- Bu teslim için ek kullanıcı onayı istenmiyor. Commit/push/PR veya herhangi
  bir sonraki entegrasyon ancak yeni açık kullanıcı talimatıyla ele alınabilir.
