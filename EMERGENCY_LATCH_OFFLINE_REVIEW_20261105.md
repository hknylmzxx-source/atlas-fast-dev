# ATLAS FAST — Acil alarm için bağımsız kalıcı kayıt prototipi

**Tarih:** 5 Kasım 2026. **Sürüm:** yalnızca `ATLAS_FAST_DEV` deneysel çalışma. **Canlı karar: NO-GO**.

## Amaç

Önceki WAL kilidi prototipinde resolver `flock` kilidini tutarken `append_incident`, STOP ve TP WAL yazıcıları `WAL_FENCE_BUSY` ile duruyordu. Bu önemli bir güvenlik açığıdır; kilidin yalnız WAL yarışı çözmesi yeterli değildir.

Yeni `emergency_latch_candidate.py` fonksiyonu, yalnızca **acil alarm olayını** ortak WAL kilidine girmeden `emergency_latch_<scope>.json` olarak `O_EXCL|O_NOFOLLOW`, `fsync` ve sıkı izin kontrolüyle kaydeder. `require_no_emergency_latch` görülen herhangi bir acil alarm işaretinde yeni işlemi durdurur. Bu kod hiç emir göndermez veya owner/runtime kilitlerini açmaz.

## Çevrimdışı kanıtlar

`tests/test_emergency_latch_candidate.py` 9 senaryo içerir. İkinci bağımsız proses, birinci prosesin WAL kilidini tuttuğu sırada alarm dosyasını başarıyla yazar. İzin ve symlink reddi, tekrar alarmın eski kanıtı değiştirmemesi, bozuk kapsam kimliği, kayıp dizin ve kalıcı giriş blokajı sınanmıştır.

**Genel sonuç: 187/187 test başarılı** (178 + 9).

## Çözülmemiş üretim riskleri

- Acil alarm dosyası mevcut Owner SDK `startup_reconcile` / `restart_protection_gate` tarafından henüz okunmuyor. Dolayısıyla **bu prototip üretimde girişleri engelliyor diye iddia edilemez**.
- Mevcut WAL kilidiyle eşzamanlı yazılmış acil işaret ve resolver arasındaki süreçler arası mutlak atomiklik hâlâ ayrıca incelenmeli. Yeni `require_no_emergency_latch` okuması tek başına TOCTOU koruması değildir.
- STOP veya TP WAL yazıcısı kilit meşgulken güvenli devam edebiliyor diye iddia edilemez. Gerçek koruyucu emir yolu henüz entegre edilmedi.
- Kalıcı alarm yazma başarısız olursa üst katmanda zorunlu fail-closed alarm/karantina davranışı ve bağımsız sistem bildirimi ayrıca doğrulanmalı.
- Binance signed read-only `query_order`, owner-private `SenderLocked` nedeniyle hâlâ bilinmiyor; güvenlik engeli bypass edilmeyecek.
- Otomatik sıfır-ENTRY WAL çözümü kapalıdır; aktif Owner SDK, izinler, canlı emir akışı ve RC1 etiketi değiştirilmedi.

**Son karar:** Ortak WAL kilidi + bağımsız alarm kanıtı, gerçek kod entegrasyonu ve STOP öncelik kontrolü bitmeden canlıya alınamaz.
