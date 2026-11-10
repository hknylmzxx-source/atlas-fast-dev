# G45 — ATLAS FAST güvenli entegrasyon sözleşmesi ve P0 kabul matrisi

Tarih: 10.11.2026. Durum: TASLAK / SADECE OFFLINE TEKNİK SÖZLEŞME. Kaynak: G44-A denetimi (commit 30a924c), G44-B gerçek owner çağrı haritası (GitHub Issue #9), G37–G43 offline adayları. Bu belge üretim yetkisi, güvenlik sertifikası veya canlı geçiş onayı değildir.

## 1. Bugünkü gerçek durum ve geçiş kapısı

Gerçek owner kodunda global_one_trade_lock tek işlem kilididir (owner_single_execution.py satır 161); run_once giriş öncesi tüm hesap pozisyonları, normal emirler ve algo emirleri sıfır olmasını ister (satır 1470–1513). main kilidi işlem ömrünce tutar (1630+). G43'ün iki-slot MockSlotStore'u bunu değiştirmez. Owner yürütücüsü G42 ve G43 modellerini import etmez; bunlar gerçek sözleşmeye bağlanmış değildir. one_write gerçek journal ve SignedVenue mutasyon lease ile çalışır; G42 ayrı callback adayıdır. production_entry kapısı mevcut fail-closed davranışıyla korunur.

**Kritik yasak:** global kilidi kaldırmak/gevşetmek, flat kontrolünü atlamak, STOP veya owner guard kapısını devreden çıkarmak, bir modelin ACKED/RESERVED sonucundan gerçek emir yetkisi çıkarmak ve eski WAL davranışının düzeldiğini varsaymak yok.

Bu sözleşmenin hedefi, gelecekte onaylanacak ayrı bir entegrasyon değişikliği için doğru arayüzleri ve olumsuz güvenlik testlerini tanımlamaktır; burada hiçbir uygulama dosyası değiştirilmez.

## 2. Sürümlü ortak kimlik / nesne modeli — öneri V1

- account_id: API anahtarı veya gizli bilgi değil; güvenilir konfigürasyondan kanonik hesap referansı.
- environment: MAINNET / TESTNET / OFFLINE; ortamlar arası hiçbir kimlik ve kayıt birbirine karışamaz.
- execution_epoch: owner/kalıcı otorite tarafından verilen monoton ve yeniden kullanılamayan restart nesli; process-local sayaç geçersiz.
- parent_scope_id: tek owner onayının ve risk politikasının bağlandığı değişmez kapsam.
- slot_id: aynı parent altında slot A veya B; tarihsel slot tekrar kullanımı yeni slot_generation olmadan yasak.
- slot_generation: kalıcı ve tekil yaşam döngüsü nesli; ABA ve yeniden giriş engeline katılır.
- venue, symbol, position_mode, position_side, entry_side: normal/hedge ayrımı ve borsa symbol eşlemesi birlikte zorunlu.
- order_role: ENTRY / STOP / TP1 / TP2; order_kind: NORMAL / ALGO; role tek başına order kimliği değildir.
- order_identity: client_id veya client_algo_id, varsa server_order_id; aynı metin iki venue/role/slot arasında izin üretmez.
- lease_epoch, lease_generation, lease_owner: kalıcı ortak fence otoritesinin verdiği token; process pid, Python nesne identity'si veya wall-clock timestamp yeterli değildir.
- risk_reservation_id: owner onay/fingerprint, exact order intent, tutar, politika sürümü ve slot nesline bağlıdır.

Kanonik bileşik anahtar: (environment, venue, account_id, parent_scope_id, execution_epoch, slot_id, slot_generation, symbol, position_mode, position_side, order_role, order_kind, order_identity). Kısaltma, boş değer, alias veya karşılaştırılamayan sürüm = fail-closed. Scope/role kısa anahtarları sadece açıkça belgelenmiş yerel legacy okumaları için geçerlidir; bağımsız iki-slot kalıcı anahtar olarak kullanılamaz.

Önerilen tipli olay zarfları: schema_version, event_id, parent_event_id, recorded_at_monotonic_source, recorded_at_utc_observed, identity (yukarıdaki bileşik kimlik), lease_epoch/generation, intent_hash, policy_hash, evidence_kind, status, provenance, WAL_sequence, durability_state, retry_policy. İleride bir serialization biçimi seçilecek, kanonikleştirme/hash/domain separation test edilecek. Bilinmeyen/eksik/tekrarlı kritik alanlar reddedilir. Modellerdeki tüm olumlu sonuçlar **authorization=false, unlock=false** varsayılanıyla taşınır; gerçek owner onayı bu zarftan türetilemez.

## 3. Yürütme ve durum sözleşmeleri

1. PRECHECK: sadece güncel, bağımsız ve imzalı/otoritatif pozisyon + normal order + algo order + account risk okuması ile; okumada gecikme, eksiklik veya uyuşmazlık => NO_NEW_ENTRY.
2. OWNER_AUTH: parent scope, slot, exact intent, total risk ve geçerlilik zamanına bağlanan açık sahibi onayı. Her slot için yetki alanı ve tek kullanımlık tüketim ayrıca tasarlanmalıdır; G43 model sonucu owner grant değildir.
3. RESERVE: slot kapasitesi ve toplam/global risk+fon aynı kalıcı otoritede atomik biçimde rezerve edilir. Önce WAL intent, sonra policy ledger geçişi iki ayrı bağımsız eylemse kopma/recovery mantığı test edilir.
4. MUTATION_FENCE: tüm giriş ve koruyucu writer'lar için sabit, belgelenmiş lock order ve generation fencing; stale token doğrulaması ile fiziksel mutasyon aynı gerçek linearization noktasında tutulamadıkça katı deadline garantisi iddia edilmez.
5. WRITE_INTENT: append-only, O_EXCL/idempotence ve fsync durumu. İhtimalli/UNKNOWN sonuç tekrar POST değildir; re-read ve manuel değerlendirme.
6. PROTECTION: entry fill bağımsız signed kanıtla doğrulanınca her slotun STOP'u ayrı kimlik/scope ile teyit edilir; gerçek exchange algo STOP ve kesintisiz coverage kanıtı gerekli. TP sağlıklı olsa bile STOP kanıtı yoksa ilgili slot QUARANTINE ve global risk kapısı değerlendirmesi.
7. SETTLEMENT: slotun terminal durumu için signed FLAT + tüm owned normal/algo emirler kesin + ledger uzlaşması + WAL resolved + STOP coverage geçmişi denetlenir. STOP açıkken TP sonucu başarı diye serbest bırakma yapılamaz.
8. RESTART: ilk durum REPLAY_ONLY / NO_NEW_ENTRY / NO_UNLOCK. Eksik kayıt ve boş store 'temiz' kanıtı değildir. Yarım fsync, ileri/geri epoch, pending intent ve ispatlanmamış fill = UNKNOWN/MANUAL_REVIEW.
9. RETIREMENT: tamamlanan slotun kimlikleri ve generation tombstone kalıcıdır; boşalan kapasite yalnız kanıtlı settlement sonrasında geri açılır. Toplam risk, kısmi fill, açık STOP, bilinmeyen TP veya incident sırasında serbest bırakılmaz.

## 4. Zamanaşımı ve ACK sözleşmesi

G41 modelinin timeout_s=0 kuralı ve G42 callback adayının timeout_s=0 nonblocking acquire kuralı tek ortak V1 tanımına indirilmeden uygulama olamaz. Aday V1 önerisi: timeout_s=0, bekleme yapmadan **tek** edinim denemesi; lease expiry denetimi sürer. Pozitif timeout_monotonic bütçesi çağrı başlangıcında belirlenir, bütün retry/sleep sonrasında ve mutasyondan hemen önce kontrol edilir. Farklı clock source veya saat damgası birimleri açıkça sürümlenmeden karıştırılmaz.

Son check ve storage mutasyonu arasındaki TOCTOU ile append sonrası fsync süresi **açık P0 kanıt açığıdır**. ACKED sadece doğrulanmış callback cevap kodudur; exchange acceptance, dayanıklılık veya sert deadline üst sınırı değildir. UNKNOWN durumunda aynı client/order POST tekrar gönderilmez. Canlı owner one_write ve ayrı protective_wal_deadline_candidate akışı aynı fonksiyon değildir: birini düzeltmek ötekinin güvenli olduğunu kanıtlamaz.

## 5. İki-slot izolasyonu ve global kilit kararı

Şimdilik owner global_one_trade_lock ve flat-account koşulu **aynen kalır**. Çoklu slot için gereken ileri tasarım: parent-level single-writer fencing; ayrı slot-scope ownership; signed position/order kapsamları; STOP/TP gözlem ve alarm partition; toplam cross-slot brüt risk/marjin limits; atomik bütçe allocation; owner approval binding; restart/settlement. Lock sırası belgelenecek ve reentrancy, deadlock, starvation ve emergency exit önceliği test edilecek. İki aynı sembolde farklı position_side işlemi borsa position mode güvenilir doğrulanmadıkça reddedilir. Tek sembolde hedge ile one-way varsayımları karışamaz. Acil STOP işlemleri giriş kilidi arızasında etkisiz kalamaz; fakat emergency permission da kontrolsüz canlı yetki olamaz.

## 6. P0 kabul matrisi (uygulama başlamadan engelleyici)

| ID | Kabul kanıtı | Negatif test / ret | Sorumlu inceleme |
| --- | --- | --- | --- |
| P0-01 kimlik | Tek V1 canonical key; iki hesap, aynı sembol LONG/SHORT, NORMAL/ALGO, yeniden kullanılan client text ve tarihsel generation ayrılır | Alan eksik, version farklı, collision ve ABA -> REJECT | Codex fixture + bağımsız Claude |
| P0-02 fence | Tüm writer'ların paylaştığı gerçek processler arası kalıcı lease generation; mutation ile geçerlilik kararı atomik | stale epoch, unlock race, after-check preemption ve deadline+lock release -> NO_LATE_APPEND veya UNKNOWN + durdurma | WAL/owner güvenlik |
| P0-03 kalıcılık | Crash/restart WAL/ledger/slot/incident birlikte yeniden kurulabilir; belirsiz fsync tekrar POST yaratmaz | fsync öncesi/sonrası kill, boş journal, torn write, duplicate, double spend -> REPLAY_ONLY | Kalıcı depolama |
| P0-04 STOP | Her slot için güncel bağımsız signed STOP active, size/symbol/side proof ve sürekli coverage | TP OK + STOP missing, stale algo, wrong-side, partial fill -> QUARANTINE | STOP/owner |
| P0-05 risk | Tek atomik global risk/fon ledger; iki process son kalan marjini aynı anda alamaz | parallel reserve, unknown fill, retire-before-settle -> DENY | Policy/ledger |
| P0-06 result bridge | Sürümlü tipli karar tüketicisi; modelden entry/unlock yetkisi türetilmez | unknown enum, missing generation, malformed receipt, mock ACKED -> FAIL CLOSED | Gateway/owner |
| P0-07 gerçek owner | Global lock/flat invariantlarından iki slota geçişe ilişkin ayrı owner onayı ve tam çağrı haritası | tek owner lock ile ikinci slot çalıştırma isteği -> DENY | Owner/Claude |
| P0-08 proof authority | Signed read-only query, snapshot currency, exchange position_mode, NORMAL+ALGO, journal causal chain | eski/çelişkili kaynak, partial/unavailable -> NO_NEW_ENTRY | Exchange adapter |
| P0-09 incident | Emniyetli STOP supervision, owner inceleme, alarm delivery, restart quarantine ve no-retry | alarm outbox failure, STOP uncertain, unreachable observer -> incident; asla otomatik unlock | Operasyon |

P0 geçişi için her satırda test adı, otomatik test sonucu, bağımsız inceleme imzası, owner değişiklik onayı, rollback/fallback notu ve gerçek implementasyon commit'i gereklidir. Sadece offline unit test sayısı bu tabloyu 'tamamlandı' yapmaz.

## 7. P1 ve P2

P1: iki farklı prosesle gerçek temp-dir/file-lock, abnormal kill, fsync injection, WAL replay, signed-response **fixture** ve G37/G38/G39/G41/G42/G43 uyumlu tüketici kontrat testleri. G40 flaky readiness fixture'ı ölçülebilir bariyere dönüştürülür; race koşulu assertion içine sahte garanti olarak konmaz. Alarm outbox durability ve gecikmiş readback testleri. G42 callback işaretleri ile gerçek owner journal ayrımı korunur.

P2: izole QA ortamı, deterministik kaynak sürümleri, gözlemlenebilirlik, security review, owner-signed read-only reconciliation, minimal difflere sahip aşamalı release planı ve rollback. Bunların hiçbiri otomatik live_enable veya owner kilit temizleme izni değildir.

## 8. Test katmanları ve çıkış kapıları

**T0** yalnız statik sözleşme/kimlik ve typed decision tests (ağ yok). **T1** POSIX temp-dir + iki gerçek süreç + kaotik schedule/partial-write ve restart (fake exchange). **T2** tüm mevcut offline testler plus zero skip ve bağımsız runner, SDK pin integrity. **T3** kullanıcıdan ayrıca izin alınmış, salt okunur gerçek Binance snapshot eşleştirme; private API yalnız açık yeniden yetkilendirmeyle. **T4** Claude bağımsız security review: owner/STOP/WAL/ledger kritik kaynak delta ve negatif senaryoları. **T5** owner'ın ayrı onayladığı kontrollü entegrasyon; sadece T0–T4 geçmiş ve live fail-closed kapısı tasarımla açılmışsa değerlendirilir. Şu an T0 sözleşme TASLAĞI; T1/T3/T4/T5 bekliyor.

## 9. Değişiklik, geri dönüş ve son inceleme paketi

G45'te yalnız bu belge hazırlanır. Mevcut owner, STOP, guard, risk, WAL uygulama kodu, testler ve politika limitleri değiştirilmez; main/dev merge, canlı emir ve gerçek SDK çağrısı yapılmaz. İleride kabul edilen bir değişiklik ayrı issue, ayrı branch, önce/sonra test kanıtı, owner açık onayı ve Claude bağımsız incelemesiyle sınırlı olmalıdır.

Claude son inceleme girişleri: G37–G43 adayları ve devirleri, G40 race kanıtı, G44_A_ENTEGRASYON_DENETIM.md, GitHub Issue #9 G44-B gerçek owner callgraph, bu G45 matrisi, izole SDK sürüm raporu, tam test logları. İnceleyici özellikle P0-01..09'u **PASS/FAIL/UNPROVEN** olarak işaretlemeli; kanıt yoksa UNPROVEN ve canlı giriş kapalı kalır.

## 10. Bugünkü karar

**NO LIVE**, **NO UNLOCK**, **NO NEW ENTRY**, **NO PROD PATCH**. G37–G44 geliştirme ve audit raporlarının tamamlanması, entegre iki-slot üretim sistemi anlamına gelmez. Sıradaki en düşük riskli iş, ortak identity/result protokolü için yalnız offline fixture ve tüketici doğrulamalarını ayrı dalda hazırlamaktır; gerçek owner global lock ve flat önkoşulu korunur.
