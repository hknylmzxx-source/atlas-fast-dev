# İ4 OneDrive çevrimiçi eşleme kontrolü — 14 Kasım 2026

## Bulgular (salt okunur)

- Mac OneDrive `SNAPSHOTS` klasörü `fileproviderctl evaluate` ile `itemIdentifier=238`, `parentItemIdentifier=235`, `isUploaded=1` olarak görünüyor.
- Sunucuda bulunan `Atlas_20261106_033005.tar.gz` (372.722.289 bayt) Mac File Provider'da `itemIdentifier=632`, `parentItemIdentifier=238`, `isUploaded=1`, `isUploading=0`; dosya sunucu envanterinde mevcut.
- Sunucuda **bulunmayan** 7, 12, 13 ve 14 Kasım tam Atlas arşivlerinin Mac File Provider'ında `parentItemIdentifier=238` (aynı klasör), `isUploaded=1`, `isUploading=0`, `isSyncPaused=0`, `isExcludedFromSync=0`, `hasUnresolvedConflicts=0`.
- 14 Kasım arşivinin File Provider `itemIdentifier=1988`, boyutu 723.198.105 bayt; sunucudaki `ATLAS_CLOUD_BACKUP/SNAPSHOTS` listelemesinde ve tam ad aramasında görünmüyor.
- 14 Kasım sentetik arşiv (`itemIdentifier=1991`, 190 bayt) sunucuya ulaştı ve bağımsız SHA-256 eşleşmesiyle doğrulandı. Bu, aynı OneDrive hesabının en azından küçük dosyaları eşitlediğini kanıtlıyor.
- `mdls` kaynak adresi (`kMDItemWhereFroms`) boş; `xattr -l` yalnız `com.apple.macl` gösterdi. Bunlar online öğe bağlantısı vermedi.
- OneDrive uygulama loglarının bulunduğu macOS `Group Containers` ve app sandbox log dizinleri bu terminal hesabına `Operation not permitted` / `PermissionError` verdi. Bu nedenle gerçek istemci hata gerekçesi doğrulanamadı.

## Yorum

Dosyalar Mac tarafında **aynı** eşitlenen klasörün altında kayıtlı. Ancak `isUploaded=1`, Microsoft Graph sunucu envanteriyle çelişiyor; bu işaretin sunucuda bağımsız bulunabilirlik kanıtı olmadığı somut olarak gösterildi. Kök neden (Mac File Provider veritabanı tutarsızlığı, istemci tarafında eşitleme kuyruğu, kota/hesap servisi vb.) hâlâ belirlenmedi. **İ4 henüz kurulu değil.**

## Sonraki güvenli adım

OneDrive uygulamasında hesap depolama kullanımını ve `Senkronizasyon sorunlarını görüntüle` ekranını kontrol et. Finder'da **14 Kasım arşivini değiştirmeden**, sağ tık → `Çevrimiçi Görüntüle` eyleminin açtığı tam URL/öğe kimliğini yakala; sunucu `SNAPSHOTS` envanterindeki klasör kimliğiyle karşılaştır. Gerçek arşivleri yeniden adlandırma/taşıma/silme ve OneDrive sıfırlama yapma. Önce bulutta doğrulanabilir bir yeni tam arşiv için güvenli kurtarma yöntemi belirle.
