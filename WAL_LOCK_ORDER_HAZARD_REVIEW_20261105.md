# ATLAS FAST — WAL Kilit Sırası ve Acil Koruma İncelemesi

**Tarih:** 5 Kasım 2026  
**Alan:** `~/Atlas/ATLAS_FAST_DEV` (yalnız geliştirme kopyası)  
**Karar:** **NO-GO FOR LIVE**

## Yeni bulgu: tek dışlayıcı kilit koruyucu yazıcıları da engelliyor

`wal_writer_fence_candidate.py` içindeki `writer_fence()` tek bir `fcntl.flock(LOCK_EX|LOCK_NB)` kilidini kullanır. Bu, izole yarış testinde atomik ENTRY/çözüm işareti davranışını kanıtlamıştı. Ancak `wal_owner_write_adapter_candidate.py` içindeki `append_incident()` ve `append_order_journal()` da aynı kilidi kullanınca iki ayrı risk beliriyor:

1. Resolver kilidi tutarken kritik **STOP / TP** jurnal yazıcısı `WAL_FENCE_BUSY` ile durur. Koruyucu STOP emir girişinden sonra acil olduğundan yeni kilit gecikmesi gerçek sisteme kabul ettirilemez.
2. Resolver kilidi tutarken **incident** kaydı da `WAL_FENCE_BUSY` ile reddedilir. Resolver markerı önce yazmışsa daha sonraki incident de `SCOPE_ALREADY_RESOLVED_NO_WRITE` ile engellenir. Bu hata üst katmanda zorunlu alarm + kalıcı yeni-entry karantinasına dönüşmezse olay görünürlüğü kaybolabilir.

Bunlar yeni saldırı veya canlı olay iddiası değil, **izole adayın gerçek güvenlik sözleşmesiyle entegrasyona hazır olmadığını** gösteren tasarım engelleridir.

## Çalıştırılan regresyonlar

`tests/test_wal_lock_order_hazards.py` içine altı test eklendi:

- Kilit meşgulken olay kaydı reddi
- Kilit meşgulken STOP kaydı reddi
- Kilit meşgulken TP kaydı reddi
- Incident önceden yazılmışsa çözüm markerı engeli
- Marker sonrasında incident yazılamaması
- Asıl Owner yürütücüsünün deneysel kilidi henüz import etmediğinin statik kontrolü

Tam paket: **178/178** çevrimdışı test başarılı. Bu başarı, kilit rekabetinin güvenli biçimde **teşhis edildiğini** gösterir; koruyucu işlemin başarıyla yürütüldüğünü değil.

## Entegrasyon öncesi zorunlu şartlar

- Kritik alarm ve STOP yazarlarının kilit rekabeti durumunda görünür, kalıcı, fail-closed bir alternatif davranışı tasarlanmalı. POST'un belirsizliğini veya tekrarlama yasağını zayıflatan bir seçenek kabul edilmez.
- `owner_single_trade.lock`, owner mutation fence, emergency intervention lock ve yeni WAL kilidi için kesin kilit sırası/deadlock kanıtı gerekli.
- Resolver, kısa kritik bölüm ve yeniden doğrulama ile sürmeli; süreli borsa sorguları WAL kilidi altında yapılmamalı.
- Emergency/incident kayıtları için scope çözümü sonrası akış test edilmeli; resolved markerı kesinlikle güvensiz bir olay gizleme yöntemi olmamalı.
- Gerçek signed `query_order` kanıtı `SenderLocked` sebebiyle mevcut değil; aşılmayacak.
- **Otomatik sıfır-ENTRY çözümü kapalı tutulacak**, aktif Owner SDK değişmeyecek.

Sonuç: Bütün WAL yazıcılarına aceleyle ortak kilit bağlamak yerine önce **acil olay/STOP güvenlik sözleşmesini** çözmek gerekiyor.
