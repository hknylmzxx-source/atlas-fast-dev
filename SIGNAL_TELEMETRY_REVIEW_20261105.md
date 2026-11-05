# ATLAS FAST — Sinyal telemetrisi ve emir durumunun ayrıştırılması

**Tarih:** 5 Kasım 2026 — **Çalışma alanı:** `ATLAS_FAST_DEV` — **Mod:** yalnızca salt-okunur ve çevrimdışı test.

## Bugünkü sinyaller

Salt-okunur `/Users/Shared/Atlas/FAST_SIGNAL_LIVE_PILOT/fast_handoff_decision_log.jsonl` analizi 5 Kasım 2026'da 3 owner-onaylı yürütücü başlatma kaydı gösterdi:

| İşlem adayı | Yön | Skor | Çıkış |
|---|---|---:|---|
| AKEUSDT | SHORT | 88.11 | `exit_code=0` |
| APRUSDT | SHORT | 89.06 | `exit_code=1` |
| AINUSDT | LONG | 86.83 | `exit_code=0` |

Bu değerler yalnızca **handoff ve yerel yürütücü olaylarıdır**. Borsada emir, gerçekleşme, kâr veya zarar varlığı kanıtlanmadı. 5 Kasım 2026 13:39:45 Türkiye saatinde en son 1000 sinyal değerlendirmesi `WAIT` ve `NO_CONFIRMED_SIGNAL` şeklindeydi; `DRY_RUN_ONLY`. Güncel strateji sinyali henüz uygun değil.

`fast_phase1_handoff_status.json` dosyası saat 13:38:29 Türkiye saati `BLOCKED_OR_WAITING` / `NO_FRESH_ELIGIBLE_PAPER_SIGNAL` bildiriyordu. Bağımsız gözlemci saat 13:39:07'de `live_release_allowed=false`, açık pozisyon=0, normal emir=0, algo emir=0, sürekli STOP doğrulaması=false ve acil flatten kanalı=false bildiriyordu.

## Yeni çevrimdışı tanılama

`signal_observability_offline.py` yalnızca çağıran tarafından sağlanan dosyalardan, üst sınırı belirlenmiş JSONL son kayıtlarını okur. Ağ, SDK, anahtar, Binance işlemi, canlı dosya mutasyonu veya oturum açma yapmaz. `tests/test_signal_observability_offline.py` içinde yedi yeni test: kısmi JSON satırı, symlink, bozuk dosya, boş log, blocker biçimi ve local exit'in exchange execution sanılmaması.

**Son toplam: 172/172 test başarılı** (önceki 165 + 7).

## Hâlâ açık işler

WAL ortak kilidi sadece izole prototip ve adapter düzeyinde sınanmıştır. Üretim `journal`, rezervasyon, incident ve marker yazıcılarına entegre edilmemiştir. Otomatik sıfır ENTRY WAL çözümü hala kapalı tutulur; RC1 değiştirilmez. Gerçek emir/sinyal ilişkisinin hesabını ancak yetkili imzalı salt-okunur Binance işlem geçmişi doğrulayabilir; `SenderLocked` aşılmaz.

**Karar: NO-GO FOR LIVE.**
