# Görev F-00/A — Test ortamı teşhisi

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

Dal: `codex/f00-ortam`. Teşhis commit'inin kimliği, commit oluşturulduktan sonra
ayrı bir kayıt commit'iyle bu bölüme eklenecektir; geçmiş yeniden yazılmayacaktır.
