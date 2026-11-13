# İ4 snapshot ve döndürme çakışması

Kurulu v4 çekirdeğinin kopyasıyla yalıtılmış ve deterministik test. `python3 -B i4_snapshot_race_probe.py /path/to/snapshot-v4-copy.py`.

- Kilitsiz: tarama dosya listesini aldıktan sonra döndürme gerçekleşti. Mevcut kayıt arşive taşındı, yeni kayıt aktif dosyaya girdi. Snapshot **OK** dedi fakat geri yüklemede yalnız yeni kayıt vardı.
- Ortak kilitli kontrol: ayrı yazıcı süreci yedek sürerken kilit alamadı. Geri yüklemede başlangıç anındaki kayıt eksiksizdi. Kilit bırakıldıktan sonra döndürme ve ekleme çalıştı.
- Gece yedeğinin tamamı boyunca yazıcıyı bekletmek üretim önerisi değildir. Önerilen sonraki aday, kısa ortak kilit altında arşivleri sabitleyip aktif dosyanın tutarlı bir kopyasını almalı, ardından bu sabit girdi setini yedeklemelidir. Yedek, beklenen parça kümesi ve özetler eksikse OK vermemelidir. Henüz uygulanmadı.
- UID 501 altında yüklü `com.atlas.fastsignal.controlagent`, `fast_signal_control_agent.py` üzerinden `run_fast_signal_live_dry_run.command` ve oradan `fast_signal_live_dry_run.py` yazıcısını çağırır. `PAUSE` dalında çağrı atlanır. Aynı yazıcının elle açılabilen 30 saniyelik `run_fast_signal_live_pilot_watch.command` yolu da var. Her iki yol ortak kilidi aynı yazıcıda kullanmalı.
- Canlı dosya, servis ve yedek yapılandırması değiştirilmedi.
