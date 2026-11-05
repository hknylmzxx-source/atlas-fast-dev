# ATLAS FAST — 5. Paket: Offline Entegrasyon Doğrulaması

**Durum:** Geliştirme dalında aday test. Canlıya alınmadı, Binance bağlantısı kurulmadı, emir oluşturulmadı.

## Kapsam

`tests/test_safety_chain_integration.py`, seçilen Owner SDK kaynak işlevlerinin AST kopyaları ve ayrı `restart_coverage` / `stop_ambiguity_guard` modüllerini tek sentetik senaryoda çalıştırır:

- Piyasa ve gelir girdileri: sahte nesneler.
- Owner onay zamanı: sahte monoton saat.
- Risk rezervasyonu ve defter kapanışı: sahte PolicyGate; gerçek yerel rezervasyon WAL-yazıcı işlevi, ancak **yalnız TemporaryDirectory içinde**.
- ENTRY/STOP: gerçek emir yazma kontrol işlevleri, ancak `post_once` sadece sahte sayaca yazar.
- STOP kanıtı: kopyalanmış `gather` / `classify`, sahte imzalı okuma cevabı.
- Yeniden başlatma: gerçek tarama/kapı kaynak işlevleri, testte sahte borsa ve politika durumu.
- Altı önceki geliştirme dosyasından farklı olarak hiçbir gerçek Owner SDK dosyası yazılmaz.

## Beş yeni senaryo

1. Rezervasyon oluşturulduktan sonra onay 15 saniyeyi geçerse giriş yapılmaz; sahte imzalı sıfır-gerçekleşme ile risk defteri kapansa dahi açık WAL yüzünden yeni giriş engellenir.
2. Fonlama/gelir okuması çökerse zero-fill kapatma kanıtı tamamlanmaz; ledger OPENED ve incident nedeniyle yeni giriş engellenir.
3. ENTRY POST sonucunda bağlantı kopar ve pozisyon açılmış olabileceği kabul edilirse POST tekrarlanmaz; STOP yokluğu ve açık pozisyon yeniden başlatmada bloklanır.
4. STOP mevcutken pozisyon açık kalırsa yeni ENTRY reddedilir. Pozisyon kapanıp STOP hâlâ açıkken de reddedilir; sonrasında STOP temizlense bile WAL çözüm işareti eksikse yine reddedilir.
5. Sahte kapanış, sıfır açık emir, defter CLOSED ve **ayrı açık çözüm kaydı** bir arada olduğunda yeniden başlatma kapısı geçebilir. Testte çözümlenme işareti açıkça ve sadece sentetik ortamda oluşturulur.

## Geliştirme açısından önemli bulgu

`policy_settle_after_flat(..., "0")` başarılı bir `PolicyGate.settle` sonucuyla `CLOSED` defter kaydı oluşturabilir. Fakat bu işlev kendi başına `resolved_<scope>.json` işareti yazmaz. `startup_reconcile` böyle bir rezervasyon WAL'sını çözümsüz sayarak yeni girişleri kapatır.

Bu bir **güvenli tarafta kalma** davranışıdır; bir sonraki geliştirme adayında, yalnızca kesin sıfır fill + güncel imzalı FLAT + sıfır kendi emir + doğrulanmış CLOSED defter + scope eşleşmesi tamamlandıktan sonra çözüm işaretinin verilip verilemeyeceği ayrıca değerlendirilmeli. Bu pakette otomatik çözüm/temizleme davranışı değiştirilmedi.

## Kanıt ve sınır

- Test: `python3 -B -m unittest discover -s tests -q`
- **72/72** çevrimdışı test, 5 yeni entegrasyon senaryosu dahil.
- Sahte `post_once` sayaçları gerçek Binance emri değildir.
- `run_once` fonksiyonunun tüm üretim bağımlılıkları uçtan uca çalıştırılmadı. Bu nedenle üretim entegrasyon güvencesi veya canlı işlem onayı verilmez.
- Fonlama, emir makbuzları ve sürekli STOP gözlemcisi üretimde gerçek entegrasyon testleriyle ayrıca değerlendirilmelidir.
- İmza kaynakları, risk limitleri ve aktif Owner SDK bu pakette değiştirilmedi.
