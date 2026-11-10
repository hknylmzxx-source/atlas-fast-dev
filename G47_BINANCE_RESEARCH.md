# G47 — Binance USDⓈ-M Futures API / ATLAS FAST karşılaştırmalı araştırma

Tarih: 10.11.2026. Kapsam: yalnız kamuya açık resmî Binance dokümantasyonu ve GitHub geliştirme kodu, gerçek hesap/API/anahtar/POST/DELETE/WS kullanılmadı. İnceleme G46 ce9cb1a tabanından yapıldı. Araştırma ajanı limit hatası nedeniyle rapor oluşturamadan durdu; bu belge bağımsız asistan incelemesidir, Codex'in tamamlanmış raporu değildir.

## Kaynak ve kanıt seviyeleri

DOKUMAN_DOGRULANDI_2026_11_10: Resmî Binance USDⓈ-M Trade REST kataloğu: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade ; USDⓈ-M User Data Streams API kataloğu: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-api/user-data-streams . Web üzerinden bu oturumda açılıp alanları incelendi. Belgeler değişebilir; içerik sürümü kesinlenmedi.

REPO_KODU_DOGRULANDI: owner_single_execution.py, G40–G46 dosyaları; kodun varlığı canlıda devrede olduğunun kanıtı değildir.

DOGRULANMADI / UNVERIFIED: USUSDT gerçek emir makbuzları, 6/9 Kasım order history ve exchange execution reason, canlı account position mode, stream eventleri, tam 2026 SDK sürümünün internetle uyumlu davranışı. Bu belge üzerinden kök neden veya canlı işlem yetkisi çıkmaz.

## Temel API gerçekleri

USDⓈ-M REST standart normal order POST /fapi/v1/order; koşullu algo order POST /fapi/v1/algoOrder. Algo isteğinde algoType=CONDITIONAL ve clientAlgoId, dönüşte algoId, algoStatus, orderType, positionSide alanları bulunur. Algo sorgusu GET /fapi/v1/algoOrder; normal emir sorgusu GET /fapi/v1/order. Açık algo emirlerini GET /fapi/v1/openAlgoOrders döndürür. Bu sorgular imzalıdır, burada çağrılmadı. Hedge/One-way değiştiren POST /fapi/v1/positionSide/dual içinde dualSidePosition=true Hedge, false One-way; değiştirme talimatı veya denemesi yapılmadı. İşlem başlatmak başka, sadece sözleşmesini okumak başkadır.

USDⓈ-M User Data Streams API'de kullanıcıya özgü stream erişimi API key ister; burada bağlantı kurulmadı. Portfolio Margin /papi/... ve Coin-M /dapi/... uç noktaları USDⓈ-M /fapi/... ile karıştırılmamalı.

## İncelenecek fark ve risk matrisi

| No | Kod/API farkı veya soru | Sonuç | Öncelik |
|---|---|---|---|
| 01 | Owner ENTRY ve TP1/TP2 normal /fapi/v1/order olarak üretiliyor | Repo kodu; emirler birbirine farklı role sahip olsa da NORMAL'dir | P0 |
| 02 | Owner STOP koşullu /fapi/v1/algoOrder kullanıyor | Repo kodu ve resmî API modeline uygun görünen sözleşme; gerçek signed active kanıtı ayrı | P0 |
| 03 | G46 ENTRY=NORMAL, STOP/TP=ALGO varsayımı; mevcut owner TP'ler NORMAL LIMIT | **Somut tasarım uyumsuzluğu**; G46 mock kuralını owner ile eşlemeden entegre etme | P0 |
| 04 | Algo clientAlgoId ile normal newClientOrderId aynı alan değil | Yanlış namespace kesin kimlik ve readback eşleştirme hatası yaratabilir | P0 |
| 05 | Normal order ID ile algoId ayrı tip; trigger sonrası actualOrderId farklı olabilir | Provenance ve işlem olaylarının çapraz bağları ayrı kurulmalı | P0 |
| 06 | AlgoStatus CANCELED bir açık emir listesinde bulunmaz | Open-list yokluğu ile kanıtlı iptal veya fill eşit değildir | P0 |
| 07 | Normal status CANCELED/EXPIRED ile algoStatus semantiği ayrı | Enum normalization + source tag olmadan tek state'e indirgeme yanlış | P0 |
| 08 | USUSDT TP iptallerinin NORMAL LIMIT mi ALGO CONDITIONAL mı olduğu | Kullanıcı gözlemi var, gerçek receipt yok; kesin neden **bilinmiyor** | P0 |
| 09 | TP reduceOnly ve mevcut pozisyon miktarı uyumu | Pozisyon azalmışsa exchange-level ret/iptal olasılığı araştırılmalı; kanıt değil | P0 |
| 10 | One-way BOTH ve Hedge LONG/SHORT konumu | Owner kodu BOTH bekliyor; F01 farklı positionSide varsayımı canlıya bağlanamaz | P0 |
| 11 | STOP closePosition=true ile quantity/reduceOnly beraber kullanılmaz | Resmî algo docs ile owner payload statik kontrolü ayrı doğrulanmalı | P0 |
| 12 | signed GET /fapi/v1/openAlgoOrders STOP active proof için gerekli olabilir | Sadece normal openOrders veya tek 404 yeterli değil | P0 |
| 13 | SDK query_order ve algo query metot imzaları | Offline imza testleri var; güncel public docs ile sürüm matrisi tutulmalı | P1 |
| 14 | User Data Stream olay sırası/tekrar/gap ve REST snapshot uzlaşması | Stream etkinliği signed REST snapshot doğruluğunun yerine geçmez | P0 |
| 15 | TP1 kısmi dolunca TP2 reduceOnly kalan miktarı aşabilir | No-retry ve aggregate qty güvenlik testi gerekir | P0 |
| 16 | Uygun order/position filter; lot tick/min notional / current market | Önceden onaylanan sembol öznitelikleri süresi dolabilir | P1 |
| 17 | Hedge için reduceOnly kullanılamayabilmesi ve closePosition yön kısıtları | Ortam position mode doğrulaması eksikse fail closed | P0 |
| 18 | Resmî dokümanda POST rate/order limits ve signed recvWindow | Retry policy yanlışsa çift POST/rate-limit riskleri | P1 |
| 19 | G42 deadline callback vs owner real journal mutation | G42 gerçek owner birleştirmesi yok; TOCTOU/fsync net değil | P0 |
| 20 | G43 F01 memory slots vs owner global one-trade lock ve full-flat kontrol | İkinci işlem reddedilir; gerçek iki-slot başarı iddia edilemez | P0 |

## USUSDT 6 ve 9 Kasım TP iptalleri: kök neden değil, hipotez listesi

H1: LIMIT reduceOnly TP miktarı, eşzamanlı fill ya da kalan pozisyonla uyumsuz hale geldi. H2: kendinle eşleşmeyi önleme/self-trade prevention veya başka exchange cancellation nedeni. H3: başka executor, stop/cleanup ya da kullanıcı müdahalesi iptal etti. H4: symbol/positionSide veya account mode değişimiyle TP yanlış scope'a bağlandı. H5: uygulama normal order status ile algoStatus'u karıştırdı veya gecikmiş readback'i iptal sandı. H6: filter/precision, post-only/order price protection veya emir zaman geçerliliği gibi borsa koşulları. Hiçbir hipotez, execution reason/transaction timeline/signed status kanıtı olmadan doğrulanmış değildir. Diğer sembollerde TP çalışması USUSDT için sorunu otomatik çözmez.

## Negatif offline fixture testleri (yeni test kodu bu görevde yazılmadı)

1. NORMAL TP yanlışlıkla ALGO kabul edilirse reddet.
2. ALGO STOP normal order_id ile aranırsa reddet.
3. clientAlgoId yerine newClientOrderId ile bağ kurulursa reddet.
4. algoId ve actualOrderId aynı sayı olsa da namespace karışmasını reddet.
5. OpenAlgoOrders listesinde olmayan STOP'u otomatik CANCELED sayma.
6. openOrders listesinde olmayan TP'yi otomatik FILLED sayma.
7. signed GET geçici 404 verirken POST tekrarını reddet.
8. algoStatus UNKNOWN iken STOP safe kabul etme.
9. NORMAL order CANCELED sonrası pozisyonun FLAT olduğunu varsayma.
10. TP partial fill + TP2 total qty aşımı için fail closed.
11. pozisyon sıfırken açık reduceOnly TP görülürse yeniden bağlama.
12. self-trade prevention expiry reason boşsa kesin neden tayin etme.
13. User Data Stream eventler ters sırayla gelirse causality/generation koru.
14. Stream kopması sonrası eski snapshot ile yeni pozisyon onayı verme.
15. BOTH account mode'da LONG/SHORT payload'a izin verme.
16. Hedge account mode'da BOTH payload'a izin verme.
17. STOP closePosition=true ve quantity birlikteyse reddet.
18. Algo closePosition=true ve reduceOnly birlikteyse reddet.
19. Aynı symbol farklı slotlardan normal/algoda client kimliği çakışınca reddet.
20. İkinci slot açılırken owner global lock aktifse DENY.
21. Risk ledger stale iken son marjin iki kez reserve edilemesin.
22. G42 deadline sonrası released lock durumunda no-late-write veya UNKNOWN.
23. G41/G42 timeout=0 yorum farkı kabul sözleşmesine uymuyorsa reddet.
24. STOP present mock boolean tek başına signed protection sayılmasın.
25. UNVERIFIED execution reason raporda CONFIRMED olarak gösterilemesin.

## Karar / sonraki güvenli iş

P0: G46 V1 order_role/order_kind doğrulamasıyla gerçek owner TP NORMAL LIMIT uyumsuzluğu incelemeye alınsın; mevcut owner ya da G46 patch edilmesin. P0: USUSDT 6/9 Kasım vakasına ait kullanıcı onaylı **salt okunur** order history, normal/algo durum, close/cancel, account position mode ve zamanlı stream evidence gerekebilir; ancak yeni veri erişimi ayrı onay ve kapsam kontrollü yapılır. P0: signed STOP active, normal+algo açık emir ve ledger/slot atomikliğine göre P0 kapıları kapanmadan iki canlı işlem etkinleştirilmez. P1: resmî doküman sürüm farkları ve SDK pin/sözleşme incelemesi Claude bağımsız son gözden geçirmesine eklenir.

**NO LIVE / NO NEW ENTRY / NO UNLOCK / NO PROD PATCH.** Rapor sadece araştırma; hiçbir Binance hesabına veya canlı emir akışına müdahale yapılmadı.
