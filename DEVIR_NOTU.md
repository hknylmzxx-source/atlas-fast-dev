# DEVIR NOTU — Claude Code

## Görev F-00/B — İzolasyon kanıt testi

- **Dal:** `claude/f00-izolasyon` (`claude/baslangic` dalından, taban `51d0965`)
- **Commit:** bu notu içeren commit (kimlik: `git log -1 claude/f00-izolasyon`)

### Tamamlanan işler

`tests/test_isolation_guard.py` eklendi (yalnızca standart kütüphane, yeni paket yok). Yöntem:

1. Tüm test paketi ayrı bir Python sürecinde, yorumlayıcı açılışından itibaren etkin olan bir koruma ile çalıştırılır. Koruma geçici bir dizindeki `sitecustomize.py` + PEP 578 audit hook'tan oluşur. `PYTHONPATH` mirasıyla testlerin başlattığı alt Python süreçleri de aynı korumayı alır.
2. Koruma şunları **engeller ve kaydeder**:
   - Her ağ bağlantısı ve DNS sorgusu (`socket.connect`, `sendto`, `sendmsg`, `getaddrinfo`, `gethostbyname*`, `getnameinfo`)
   - `/Users/Shared/Atlas/` ve `~/Atlas/AKTIF_*` yollarına dosya erişimi: `open`, `listdir`, `scandir`, `rename`, `remove`, `shutil.*`, `glob` vb. Karşılaştırma büyük/küçük harfe duyarsızdır (APFS); realpath ve `/System/Volumes/Data` öneki de hesaba katılır. Bu yolları içeren alt süreç komutları da engellenir.
   - Canlı Owner modüllerinin importu (meta_path bulucu ile)
3. BINANCE / API_KEY / APIKEY / SECRET içeren ortam değişkeni okumalarını **kaydeder**. Bunun için `os.environ` nesnesinin sınıfı değiştirilir, nesnenin kendisi aynı kalır. Yalnızca değişken adı kaydedilir, değer asla kaydedilmez.
4. Canlı modül listesi kaynaktan AST ile çıkarılır. Stdlib dışı, depoda dosyası olmayan ve herkese açık 3. taraf paket olmayan importlar alınır, AGENTS.md'deki `fast_signal_live_executor` eklenir. Ortaya çıkan 8 modül: `fast_r8_offline_integration_contract_F3F4_CANDIDATE`, `observer_liveness`, `owner_mutation_fence`, `live_pilot`, `sdk_signed_sender`, `exact_trade_approval`, `r2_guard_venue`, `fast_signal_live_executor`. Kaynakta yeni bir canlı import belirirse test kırmızı olur.
5. Paket bir kez korumasız, bir kez korumalı çalıştırılır. Test sayısı, ERROR/FAIL kümesi ve çıkış kodu aynı olmalıdır. Bu, korumanın mevcut testlerin davranışını değiştirmediğini gösterir.
6. Statik kontrol: depodaki `.py` dosyalarında yasak yol sabiti yok.
7. Öz-test: Koruma sahte yasak kök dizinlerle denenir. Ağ, yol, import ve ortam değişkeni ihlallerinin gerçekten engellendiği ya da kaydedildiği doğrulanır; alt-alt süreç kapsamı da sınanır. Gerçek yol yalnızca var olmayan bir dosya adıyla denenir. Ağ denemesi yereldir (127.0.0.1:9, localhost).
8. Koruma ana test sürecine **kurulmaz**, çünkü audit hook geri alınamaz. Böylece normal test çalışması etkilenmez.

### Bulgular (mevcut kodda ihlal)

- **İhlal bulunmadı.** Korumalı çalışmada 19 süreç izlendi: paketi çalıştıran 1 süreç ve testlerin başlattığı 18 Python alt süreci. Hepsi korumayla başladı. Ağ, yasak yol, canlı modül importu ya da hassas ortam değişkeni okuması kaydı: 0.
- **Gözlem:** `tests/test_sdk_transport_offline.py:142`, herkese açık Binance SDK paketi `binance_sdk_derivatives_trading_usds_futures`'ı import etmeye çalışıyor. Amaç yalnızca sınıf imzasını kontrol etmek; örnek oluşturulmuyor, ağ yok. Bu bir ihlal sayılmadı, bilgi olarak kaydedildi.
- **Gözlem:** `owner_single_execution.py:59` dosyanın en üstünde `import requests` içeriyor. Testler bu modülü import etmiyor (AST ile okunuyor), bu yüzden çalışma zamanında etkisi yok.

### Çalıştırılan testler

- `python3 -B -m unittest tests.test_isolation_guard -v` → 15 test, **OK**
- `python3 -B -m unittest discover -s tests -v` → 355 test, **FAILED (errors=2)**
  - Bu 2 hata önceden vardı; bu değişiklikten önce de (340 testte) aynıydı:
    `test_sdk_transport_offline.InstalledBinanceSdkContracts.test_exact_sdk_dependency_versions` ve
    `test_actual_rest_class_offers_signed_gets`. Sebep: `binance-sdk-derivatives-trading-usds-futures` paketi bu ortamda kurulu değil (`ModuleNotFoundError`). Görev gereği düzeltilmedi.
  - Yeni eklenen 15 testin tamamı geçti.

### Açık sorunlar ve riskler (kapsam sınırları)

- `os.stat`, `os.path.exists` ve `Path.exists` için Python audit olayı yok. Bu tür salt varlık kontrolleri çalışma zamanında yakalanamaz; statik kaynak taraması bu açığı kısmen kapatır.
- Hazır `posix.environ` sözlüğüne doğrudan erişim izlenmez. Depoda böyle bir kullanım yok.
- C eklentileri ve `ctypes` ile yapılan doğrudan sistem çağrıları kapsam dışıdır.
- `-I`, `-E`, `-S` bayraklarıyla ya da özel `env=` ile başlatılan alt Python süreçleri korumayı almayabilir. Bunlar `uncovered_child` olarak raporlanır ve test başarısız olur. Şu an böyle bir süreç yok.
- 19 sürecin 3'ünde `exit` kaydı yok. Bunlar testlerin kendisinin sonlandırdığı alt süreçler (`proc.kill`). İhlal kayıtları anında yazıldığı için bu durum sonucu etkilemez.
- 2 eski hata (SDK paketi kurulu değil) açık duruyor. Paket kurulumu kullanıcı onayı gerektirir.

### Sonraki güvenli adım

- Codex'in `tests/test_isolation_guard.py` için çapraz incelemesi (AGENTS.md madde 6).
- `claude/f00-izolasyon` → `main` için Pull Request açılması önerilir.

### Gerekli kullanıcı onayları

- PR açılması ve birleştirme (yalnızca kullanıcı).
- Eski 2 hatanın giderilmesi için SDK paketinin sabit sürümle kurulup kurulmayacağı kararı.
