# ATLAS FAST — Offline Release Candidate 1

**Sürüm adayı:** `atlas-fast-v0.1.0-rc1-offline-20261105`  
**Tarih:** 5 Kasım 2026  
**Mac:** `~/Atlas/ATLAS_FAST_DEV`  
**Git dalı:** `dev/20261105-preflight-diagnostics`  
**Durum:** **GELİŞTİRME ADAYI / NO-GO FOR LIVE**. Bu etiket herhangi bir canlı işlem veya kurulum izni vermez.

## Kapsam ve kaynak geçmişi

Ana geliştirme dalında `44e3682` ilk çevrimdışı kaynak anlık görüntüsü; ardından yedi ayrı geliştirme paketi:

| Paket | Commit | Konu |
| --- | --- | --- |
| 1 | `ed0d6c2` | Piyasa verisi zaman teşhisi, doğru risk/kaldıraç ret mesajları |
| 2 | `b24ad29` | İmzalı gelir sorgusundan sonra yeni market snapshot ve hata süresi ölçümleri |
| 3 | `836d676` | 15 saniyelik owner onayı, rezervasyon ve ENTRY öncesi fail-closed kontroller |
| 4 | `2e1b420` | Ağ belirsizliği, STOP, WAL, yeniden başlatma güvenlik testleri |
| 5 | `5b9f80d` | Sahte borsayla tüm güvenlik zinciri entegrasyon testi |
| 6 | `be44eee` | Kesin sıfır-ENTRY halinde imzalı kanıt şartlarına bağlı rezervasyon WAL çözümü |
| 7 | `269217a` | Gerçek kurulu Binance SDK sürümleri ve ağsız HTTP koruma sözleşmesi testleri |

## Yerel doğrulama

Komut:

```sh
cd "$HOME/Atlas/ATLAS_FAST_DEV"
"$HOME/Library/Application Support/AtlasFastOwnerSDK/.venv/bin/python" -B -m unittest discover -s tests -q
git diff --check
```

**115/115 test başarılı (5 Kasım 2026).** Bunlar sentetik borsa cevapları, kaynak işlevleri ve gerçek kurulu Python/SDK paketleriyle yapılan **ağsız** testlerdir. Binance gerçek hesap GET/POST veya üretim `run_once` zinciri yürütülmedi. Altı aktif Owner SDK kaynak dosyası başlangıç SHA-256 özetleriyle karşılaştırılıp değişmemiş bulundu.

`RC_SOURCE_HASHES_20261105.json` adayın seçilmiş kaynak/test dosyalarını SHA-256 ile tanımlar. Aktif Owner SDK ve risk limitleri değiştirilmedi.

## Sürüm adayının dağıtım kapsamı

Bu yalnızca **yerel Git etiketi** ve gözden geçirilmek üzere hazırlanmış doküman paketidir. GitHub'a gönderilmez; aktif Owner SDK üzerine kopyalanmaz; otomatik canlı işlem izni, emir üretimi, emir silme veya risk değişikliği içermez.

Kurulu SDK arayüzleri `binance-sdk-derivatives-trading-usds-futures==17.6.0` ve `binance-common==4.5.1` sürümleriyle eşleşti. Gerçek read-only `query_order` denemesi `sdk_signed_sender.SenderLocked` tarafından reddedildi. Güvenlik kilidi aşılmadı.

## Açık engeller — NO-GO

1. **Gerçek Binance GET / `-2013` emrin yokluğu kanıtı yok:** `SenderLocked`. Paket 6 WAL otomatik çözümünü üretime taşımak için imzalı canlı read-only davranış ayrı ve yetkili ortamda doğrulanmalı.
2. **Tam `run_once` uçtan uca test edilmedi.** Fonksiyonlar AST üzerinden ve sanal nesnelerle seçili aşamalarda test edildi; üretim çalışma başlatma, kaynak yönetimi ve anlık kesintiler için ayrıca kapsamlı staging doğrulaması gerekir.
3. **Sürekli STOP koruması doğrulanmış değil:** bağımsız gözlemci `continuous_stop_coverage_verified=false`, `emergency_flatten_connected=false` bildirmektedir. Read-only FLAT, canlı güvenlik uygunluğu değildir.
4. **Kod incelemesi ve sürüm farkı analizi:** mutable WAL/defter kanıtları, yarış koşulları, farklı saat tabanları, restart ve hata enjeksiyonlarının bağımsız incelenmesi gerekir.
5. **Canlıya alma için açık ve ayrı kullanıcı onayı, güvenlik kilitlerinin geçerli olması, geri dönüş planı, sürüm bütünlüğü ve yazma izni gözden geçirmesi zorunludur.** Bu RC, bu izni üretmez.
6. **Eski manuel AINUSDT finansal denetimi:** BNB komisyonunun tam tarihsel USDT dönüşümü açıklanmadı; hesap raporlama doğrulamasında istisna olarak açık tutulmalıdır.

Karar ayrıntıları: `GO_NO_GO_CHECKLIST_20261105.md`, `SDK_OFFLINE_INTEGRATION_20261105.md`, `ZERO_ENTRY_WAL_RESOLUTION_20261105.md`.

## Güvenlik

Bu dosya ve yerel Git etiketi canlı borsa emirlerini asla başlatmaz. Canlı Owner SDK, API anahtarı, Keychain, WAL, risk defteri veya mevcut pozisyonlar bu geliştirme akışı sırasında değiştirilmez.
