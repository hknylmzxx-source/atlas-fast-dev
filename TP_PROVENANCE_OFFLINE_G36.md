# ATLAS FAST — Görev 36: TP kaynağı ve birleşik güvenlik testleri

Dal: `codex/f00-ortam`. Başlangıç commit'i:
`349e772593d7332fa1838af6b268610d6dbcae4c`.

## Değişiklik ve sınırlar

`tp_provenance_offline.py`, yerel plan ile sentetik Binance
`ORDER_TRADE_UPDATE` olayı ve ayrı mock/replay emir okumasının tutarlılığını
denetler. Üretim Owner akışına bağlanmaz; mevcut Owner, STOP, R2, risk tavanları
ve kilit uygulamaları değiştirilmez. Yeni bağımlılık yoktur.

Denetleyici yalnız `TP1`/`TP2`, `LIMIT`, `GTC`, `BOTH`, reduce-only planı kabul
eder. Planın kopyasını alır; olayın sembol, client ID, yön, emir türü, miktar
ve fiyatını bu kopyayla eşleştirir. Gerçekleşme miktarı, olay türü ve
NEW/PARTIALLY_FILLED/FILLED durumu birbirini doğrulamalıdır. Bozuk sayılar,
sonlu olmayan değerler, boolean emir kimlikleri/saatler, eski veya gelecek
olaylar reddedilir.

Her kabul için planın `symbol` ve `orig_client_order_id` değerleriyle ayrı
`read_order(..., recv_window=5000)` mock çağrısı gerekir. Bu çağrı
`(emir_dict, alınma_zamanı_ms)` döndürür. Yanıtın planla ve olayla emir kimliği,
güncelleme zamanı, gerçekleşme durumu ve miktarı dahil eşleşmesi gerekir.
Timeout, bulunamayan emir, eksik yanıt veya eski okuma kabul edilmez. Okumanın
alınma zamanı olayın yayım zamanı `E` değerinden önce olamaz. Olaydaki
`source`, `signature`, `authenticated`, `owner_approved` veya gömülü makbuz
bu okumanın yerine geçmez.

Kabul edilmiş olayın ilerlemesi nesne belleğinde tutulur. Aynı/eski zaman,
emir kimliği değişimi, dolum gerilemesi veya ilerlemesiz tekrar reddedilir.
Reddedilen olay ya da başarısız okuma durumu ilerletmez. FILLED sonrasında
emir yeniden açılamaz.

## Kanıtın anlamı

Başarılı sonuç yalnız `synthetic_tp_consistent=True` anlamına gelir. Ayrı
mock kanalından dönen veri de çağıranın sağladığı sentetik veridir; gerçek
Binance imzası, hesap kimliği veya sunucu kaynağı doğrulanmış sayılmaz.
Birbiriyle eşleşen iki sahte belge gerçek kaynak doğrulaması oluşturamaz.
Her sonuçta aşağıdaki alanlar sabit `False` döner:

- `authenticated_signed_get_verified`
- `entry_authorized`
- `may_clear_latch`
- `may_release_owner_lock`

Denetleyici disk veya ağ kullanmaz, emir göndermez, kilit almaz/açmaz.
Çağıran, `read_order` için yalnız offline mock/replay sağlamalıdır; bu genel
Python callback'i sandbox veya canlı ulaşım engeli değildir.

Bellekteki tekrar kontrolü yeniden başlatma ya da birden fazla süreç arasında
kalıcı koruma sağlamaz; aynı nesnenin eşzamanlı kullanımı desteklenmez.
Aynı milisaniyede gelen farklı güncellemeler de muhafazakâr biçimde reddedilir.
Olay ve REST makbuzu arasında yeni dolum oluşması halinde tam eşleşme
sağlanmaz; bu aday gerçek zamanlı yeniden uzlaştırma uygulaması değildir.

## Test kapsamı

`tests/test_tp_provenance_offline.py` plan–olay–okuma bağını, sahte kaynak
iddialarını, bozuk alanları, zaman/miktar sınırlarını ve tekrar/dolum sırasını
sınar.

`tests/test_tp_safety_integration_offline.py` mevcut uygulamalarla birlikte
şunları sınar:

- TP kaydı ve incident varken sahte onay veya eşleşen TP olayı scope'u çözmez.
- Başka bir yerel test sürecinin tuttuğu gerçek POSIX fence, geçerli/sahte TP
  olayı işlense de tutulmaya devam eder; inode ve WAL içeriği korunur.
- Mevcut fence, hardlink/symlink kilitleri reddeder; hedef veri değişmez.
- Hata sonrasında yalnız testin kendi tuttuğu fence serbest kalır.
- Owner kaynak işlevleri AST ile seçilir; ayrı kesin `query_order` zorunludur.
  Sahte POST makbuzu başarısız GET'i telafi etmez. Altı mock GET denemesine
  rağmen POST bir kez kalır; lease bırakılır ve belirsiz WAL korunur.
- Fence meşgulse TP için sıfır mock POST olur, lease bırakılır; fence serbest
  kalınca aynı test çağrısı ilerleyebilir. Bu, mevcut koruyucu emir gecikmesi
  tasarım engelini ortadan kaldırmaz ve canlı entegrasyon onayı oluşturmaz.

Canlı Owner/SDK modülü import edilmez; yalnız kaynak AST'sinden seçilmiş
işlevler ve geçici dizinler kullanılır. Çalıştırılan test sonuçları,
bağımsız çapraz inceleme ve commit kimlikleri `DEVIR_NOTU.md` içindedir.
