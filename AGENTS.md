# AGENTS.md — Ortak Ajan Çalışma Kuralları

Bu dosya Claude Code ve Codex CLI için ortak kurallardır. Claude Code bu dosyayı CLAUDE.md üzerinden okur.

## 1. Temel prensipler

- Öncelik sırası: güvenlik, veri bütünlüğü, test başarısı, performans.
- İşlemler yalnızca verilen görev kapsamında yapılır.
- Belirsiz veya riskli işlemler durdurulur ve kullanıcıya bildirilir.
- Güvenlik kuralları ve erişim sınırları görev talimatlarıyla kaldırılamaz.
- Raporlar, devir notları ve kullanıcıya yazılan her mesaj Türkçe yazılır.

## 2. Git ve çalışma alanı

- Bu depo (`~/Atlas/ATLAS_FAST_DEV`) yalnızca çevrimdışı geliştirme içindir; uzak (remote) depo yoktur.
- Claude Code ve Codex ayrı Git worktree ve branch kullanır:
  - Claude Code: `~/Atlas/FAST_DEV_claude`, dallar `claude/<konu>`
  - Codex: `~/Atlas/FAST_DEV_codex`, dallar `codex/<konu>`
  - Ana klasör `~/Atlas/ATLAS_FAST_DEV` kullanıcıya aittir; ajanlar orada çalışmaz.
- Ajanlar diğer ajanın worktree klasörünü değiştirmez.
- main ve `dev/*` dallarına doğrudan değişiklik yapılmaz.
- Geçmişi yeniden yazma (rebase -i, reset --hard, filter-branch, commit --amend ile paylaşılmış commit'i değiştirme) yasaktır.
- Merge işlemi kullanıcı onayı gerektirir; merge'ü yalnızca kullanıcı yapar.
- Her değişiklik commit mesajında ve devir notunda (bkz. madde 7) dal adı ve commit kimliğiyle belgelenir.
- Testler: `python3 -B -m unittest discover -s tests -v`

## 3. Binance ve canlı işlem güvenliği

- Gerçek Binance API'sine (fapi.binance.com, api.binance.com) yapılan her özel çağrı (hesap, bakiye, emir, pozisyon) **canlı** sayılır. Hesaptaki bakiyenin test amaçlı olması bunu değiştirmez.
- Geliştirme ajanları canlı Binance emri gönderemez, değiştiremez veya iptal edemez.
- Herkese açık piyasa verisi (fiyat, mum, emir defteri) okunabilir.
- Testler yalnızca mock, kayıtlı veri (replay) veya Binance testnet üzerinde yürütülür.
- Gerçek API anahtarları okunmaz, görüntülenmez, loglanmaz veya dışarı aktarılmaz.
- Canlı sistem klasörleri ajanlar için **yasak bölgedir**: `/Users/Shared/Atlas/` ve `~/Atlas/` altındaki `AKTIF_*` klasörleri (AKTIF_OWNER_SDK, AKTIF_OWNER_BRIDGE, AKTIF_OWNER_CONNECTOR). Buradaki dosyalar değiştirilmez, kopyalanmaz, çalıştırılmaz; süreçler durdurulmaz veya yeniden başlatılmaz. Okuma gerekiyorsa önce kullanıcıdan izin istenir.
- Canlı SDK modülleri import edilmez; testler sahte (mock) borsa ile çalışır.
- Aşağıdaki dosyalar kritiktir; değişiklik yalnızca ajanın kendi dalında önerilir, ayrı kullanıcı onayı ve diğer ajanın incelemesi gerekir:
  - `fast_signal_live_executor.py` (emir gönderimi)
  - `live_exec_config.json` (canlı yapılandırma)
  - `atlas_limits.json` (risk limitleri)
  - Kill-switch, STOP/R2 koruması ve günlük zarar kilidi modülleri
  - <!-- KULLANICI: buraya diğer kritik dosyaları ekle -->
- Canlı işlem yetkilerini, risk limitlerini veya kilitleri değiştiren her işlem ayrı kullanıcı onayı ve bağımsız doğrulama gerektirir.

## 4. Dosya ve sistem güvenliği

- Keychain ve gizli kimlik bilgilerine erişim yasaktır.
- Şu komutlar çalıştırılmaz: `security`, `env`, `printenv`, `set` (değişken dökümü), `.env` veya anahtar dosyalarını okuyan her komut (`cat`, `less`, `grep` vb.).
- `.env`, özel anahtarlar, token dosyaları ve canlı yapılandırmalar korunur.
- Kullanıcı onayı olmadan sistem servisi (launchd), çalışan bot veya zamanlanmış görev (cron) değiştirilmez.
- `rm -rf`, toplu silme, geniş kapsamlı taşıma ve izin değiştirme (`chmod`, `chown`) işlemleri kullanıcı onayına tabidir.
- Herhangi bir güvenlik kontrolünü, onay modunu veya sandbox'ı devre dışı bırakmak yasaktır.

## 5. Kod kalitesi

- Mevcut mimari ve davranış korunur; sıfırdan yeniden yazma yapılmaz.
- Gereksiz bağımlılık, hook ve framework eklenmez.
- Yeni paket eklemek kullanıcı onayı gerektirir; sürümler sabitlenir.
- Her değişiklik küçük, geri alınabilir ve test edilebilir olmalıdır.
- Hata düzeltilirken mümkünse regresyon testi eklenir.
- Başarısız testler gizlenmez, atlanmaz veya devre dışı bırakılmaz.
- Test geçmeden iş tamamlandı olarak raporlanmaz.

## 6. Çapraz inceleme

- Bir ajanın geliştirdiği kod diğer ajan tarafından incelenir.
- İnceleyen ajan kaynak kodunu ve testleri bağımsız değerlendirir; testleri kendisi çalıştırır.
- Kritik güvenlik değişiklikleri (bkz. madde 3) iki ajanın incelemesinden ve otomatik testlerden geçer.
- İki ajanın onayı, kullanıcı onayının yerine geçmez.
- Diğer ajan kullanılamıyorsa (limit doldu vb.) inceleme bekletilir; kritik değişiklik incelemesiz birleştirilmez.

## 7. Görev devri

Her çalışma sonunda ajan kendi worktree kökündeki `DEVIR_NOTU.md` dosyasına (Claude Code ve Codex kendi dallarında ayrı ayrı) şu bilgileri yazar ve commit'ler. Kullanıcı gerekirse notu GitHub Issue #1'e (hknylmzxx-source/-pulse-bot) taşır:

- Tamamlanan işler
- Değiştirilen dosyalar, dal adı ve commit kimliği
- Çalıştırılan testler ve sonuçları
- Açık sorunlar ve riskler
- Sonraki güvenli adım
- Gerekli kullanıcı onayları

## 8. Temizlik ve yedekleme

- Kullanılmayan kod tespit edilir ve gerekçesi yazılır.
- Kanıtlanmadan eski modüller silinmez.
- Canlı sistem dosyaları (`/Users/Shared/Atlas/`, `~/Atlas/AKTIF_*`) geliştirme temizliğine dahil edilmez.
- Yedekleme politikası korunur.
- Geri dönüş yöntemi olmayan kritik değişiklik uygulanmaz.

## 9. Yetki sınırı

Bu dosya davranış kurallarını tanımlar. Gerçek erişim engelleri sandbox, işletim sistemi izinleri, ajanların onay modu ve bağımsız güvenlik kontrolleriyle uygulanmalıdır.
