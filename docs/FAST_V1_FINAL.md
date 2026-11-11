# ATLAS FAST v1.0 — Final (mühürlü canlı sürüm)

Bu commit, 11.11.2026 15:42 (İstanbul) itibarıyla canlıda (`~/Library/Application Support/AtlasFastOwnerSDK`) çalışan FAST owner dosyalarının **birebir** kopyasıdır. Etiket: `fast-v1.0-final`.

## Canlı dosya hash'leri (SHA256, canlıyla birebir doğrulandı)
| Dosya | SHA256 |
|---|---|
| owner_single_execution.py | 2f4727cb3240618e87a141728e6d89f0645049d2fa56cc9b09a8bd71f5a63a2f |
| owner_continuous_handoff.py | 4d0376985b2ae25b17a9fc3baa8ddd3e9243d7c3106a50700f29de230c95b321 |
| sdk_rpc_worker.py | 3055c39ef8f33627139fb133841a81b5f4282e879c485b65727facdace2d4400 |
| tp_exposure_monitor_g56.py | 8bd6d649b8005781c8b20d611e0f8f00d47eab049748c8e5acf0ce933aec8037 |
| fast_telemetry_g56.py | fad6c0e2b55376de0591fee3dc9202c0846520850ed425dcdba287c296dcfc8e |
| tp_exposure_guard_offline_g55.py | 470bdaef3a7ed456bca844e87ce119dfe0b31c2f3440f44490f053b1793a2185 |
| stop_ambiguity_guard.py | 1b4606790d70421cad4bd708c082b01294424ae433f5fe981cee8c975214d191 |
| restart_coverage.py | 239d74b69b25001782e26226281081bbb8cbb88a735e81172a3bf6d161e31f82 |
| order_filters.py | 6a9585b47b56426f227ee50a8aadde7a371bd7e3ce5d34170a7998bddcd28bef |
| policy_integration.py | 877d9030950aa6d03f31d60e44d622572888ae9ef74173335ffc849b41104b91 |
| atlas_integration_inputs_core.py | 1e071e39c6c7a6a982450e4bd4d577e2eb49a7fb5fe77352e6da882e89000110 |

Canlı SDK klasöründeki diğer modüller (risk_bridge, risk_ledger, live_pilot, sdk_signed_sender, alarm_quarantine, mail_alarm_sink, owner_phone_* vb.) bu depoda anlık görüntü olarak tutulmuyor; 44e3682'deki seçili-dosya yaklaşımı korundu.

## Kurulum
- Tarih: 11.11.2026 15:42, FAZ3C kurulum betiği (`FAST_KUR.command`), pozisyon FLAT iken.
- Yedek: `~/ATLAS_SAHIP/yedek_FAZ3C_20261111_154223`
- Sonrasında `owner-continuous-handoff` ve `owner-sdk-offline` servisleri yeniden başlatıldı.
- Geri alma: `~/ATLAS_AUDIT_READONLY/analiz_20261111/faz3c/kurulum/FAST_GERI_AL.command`

## İçerik (44e3682'ye göre)
- B3 (7 Kas): kayıpta giriş kilidi yalnız 1,00 USDT üstü gerçekleşen kayıpta (`LOSS_LOCK_USDT`).
- G56: pozisyon açıkken ≤30 sn'de bir salt-okuma "TP'siz açık pozisyon" denetimi; ihlalde mevcut owner alarm kanalı (SafeAlarm → alarm_log → Mail). 5 dk tekilleştirme. Emir göndermez/iptal etmez.
- G56: emir yaşam döngüsü telemetrisi (`fast_trade_telemetry/`, eklemeli JSONL, WAL'dan ayrı).
- Executor pin güncellemesi: `owner_continuous_handoff.py` ve `sdk_rpc_worker.py` içinde 1'er satır.
- Strateji, risk sabitleri (MAX_RISK vb.), STOP/TP yerleşimi, SignedVenue, one_write, kilitler: değişmedi (AST ile teyit).

## Dışarıda bırakılanlar
- G38–G55 zinciri (WAL kilidi, onay süresi, işaret kapısı vb.): birleştirilmedi, dallarda arşiv.
- F-01 (iki eşzamanlı işlem): ertelendi.
- Giriş öncesi fiyat kancası (`run_once` değişikliği gerektirir): yok; slippage ölçümü eksik.

## Faz 5 — gözlem kuralı (önceden kayıtlı)
- Süre: 4 hafta veya 100 işlem (hangisi önce). Bu sürede kod değişikliği yok.
- Kenar oranı ≥ 2 ve PF > 1,3 → büyütme konuşulur.
- PF < 1 → mikro pilot olarak kalır ya da kapatılır; sahip seçer. Geliştirme yeniden açılmaz.

## Testler
`tests/`: G56 telemetri, TP denetim modülü ve canlı varyant entegrasyon testleri — Python 3.13 ile 30/30 OK. (`test_tp_exposure_guard_g55` canlıda olmayan G37 offline modülüne bağımlı olduğu için alınmadı.)

## Raporlar
`~/ATLAS_AUDIT_READONLY/analiz_20261111/`: KENAR_ORANI_20261111.md, FAST_DURUM_TESPITI_20261111.md, faz2/, faz3/, faz3b/, faz3c/ (FAZ3C_RAPOR.md, canli_vs_yeni.diff, kurulum/KURULUM_LOG_20261111_154223_kur.txt).
