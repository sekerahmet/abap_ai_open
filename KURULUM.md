# ABAP AI IDE — Kurulum (Windows)

Bu belge hazır `main.exe` ile kurulum içindir. Python kurmak gerekmez.

## 1. Dosyaları indir

GitHub → **Releases** → en son sürüm → `main.exe` dosyasını indir.
Örneğin `C:\ABAP_AI\` gibi bir klasöre koy.

## 2. SAP RFC kütüphanesi (zorunlu)

Uygulama SAP'ye SAP'nin kendi RFC kütüphanesi (NetWeaver RFC SDK) ile bağlanır. Bu kütüphane
lisans gereği exe'nin içine konmaz; şu 4 dosya `main.exe` ile **aynı klasörde** olmalı:

```
sapnwrfc.dll
icudt50.dll
icuin50.dll
icuuc50.dll
```

Bunları ya SAP Support Portal'dan (S-user gerekir, "SAP NetWeaver RFC SDK 7.50", `lib` klasörü)
ya da SDK'sı kurulu bir arkadaşından alabilirsin. Eksikse uygulama açılır ama Fetch'te
"RFC Connection Failed" verir.

## 3. İlk açılış

1. `main.exe`'yi çalıştır (SmartScreen uyarısı çıkarsa *Daha fazla bilgi → Yine de çalıştır*).
2. Üst çubukta **⚙** simgesine tıkla, bağlantı profilini doldur:
   App Server, System Nr, Client, User, Password, gerekiyorsa SAP Router. **Save & use**.
3. Tip olarak *Program* seç, nesne adını yaz (ör. `ZFI_CO_003`), **Fetch**.
   Sağdaki *SAP Objects* ağacı programın kullandığı tabloları, include'ları, class'ları listeler;
   tek tık satıra gider, çift tık nesneyi açar.

Tüm veriler `%APPDATA%\ABAP_AI\` altında tutulur (profiller, önbellek, loglar).
Bağlantı **salt okunur**dur; SAP'ye hiçbir şey yazılmaz.

## 4. İsteğe bağlı: GitHub senkronu (Push / Pull)

Workspace'i özel bir GitHub reposuyla senkronlamak için `main.exe`'nin yanına `.env` dosyası koy:

```
GITHUB_REPO=https://github.com/<kullanici>/<ozel-workspace-repo>
GITHUB_TOKEN=github_pat_...
```

Token: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained →
Repository access: sadece o repo → Permissions: **Contents: Read and write**.
`.env` yoksa Push/Pull dışında her şey çalışır. Token'ı kimseyle paylaşma.

## 5. İsteğe bağlı: Claude sekmesi

Claude Pro/Max aboneliğiyle çalışır, API anahtarı gerekmez.

1. PowerShell aç: `winget install --id Anthropic.ClaudeCode -e`
2. Yeni bir PowerShell aç, `claude` yaz, aboneliğinle tarayıcıdan giriş yap, `/exit`.
3. IDE'de soldaki CLAUDE panelinde **+ New session** → sekme açılır. Claude workspace'teki
   dosyaları okuyabilir. Oturum listesinde sağ tık: yeniden adlandır / sil.

Claude'un SAP'ye de canlı erişmesi (MCP araçları) için Python tarafı gerekir:

1. Python 3.12 kur, kaynak kodu indir (`git clone https://github.com/sekerahmet/abap_ai_open`)
2. O klasörde `pip install -r requirements.txt`
3. `main.exe`'yi o klasörün içine (veya `dist\` altına) koy; IDE `mcp_server.py`'yi kendisi bulur.
   Ya da Claude Desktop kullanıyorsan oradaki MCP tanımını otomatik alır.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| Uygulama hiç açılmıyor | `%APPDATA%\ABAP_AI\crash.log` dosyasına bak |
| "RFC Connection Failed … sapnwrfc" | 2. adımdaki DLL'ler exe'nin yanında değil |
| "Name or password is incorrect" | Profildeki kullanıcı/şifre/client yanlış |
| Push: "token geçersiz" | `.env` içindeki GITHUB_TOKEN süresi dolmuş, yenile |
| Claude: "CLI not found" | 5. adım; kurulumdan sonra IDE'yi yeniden aç |

## 6. SAP olmadan çalışmak: "Local (no SAP)" profili

Profil kutusundan **Local (no SAP)** seçince WORKSPACE paneli serbest bir klasör ağacına dönüşür:
`%APPDATA%\ABAP_AI\workspace\Local (no SAP)\`. Burayı istediğin gibi düzenle:

- Sağ tık → **New folder / New file / Import files here / Rename / Delete**, ya da dosyaları
  ağacın üstüne sürükle-bırak, ya da 📂 ile Explorer'da açıp elle düzenle (2 sn içinde yenilenir).
- **Open file…** / **Paste code** seçili klasöre ekler.
- Her dosya çift tıkla açılır; Edit → Save aynı dosyaya yazar.
- Claude'a bir dosyayı düzeltmesini söylersen öneriyi **aynı klasördeki `proposals/`** altına
  yazar (ör. `reports/ZFI_X.abap` → `reports/proposals/ZFI_X.abap`); IDE otomatik olarak
  Diff sekmesi açar. Öneriyi beğenirsen Diff → "Open proposal code" → Copy ile kendin uygularsın;
  dosyalarına Claude hiçbir zaman doğrudan yazmaz.
- Bu profil için SAP bağlantısı, DLL ya da `.env` gerekmez (Claude için yine Claude Code CLI
  ve `write_proposal` için MCP sunucusu = Python kurulumu gerekir; yoksa Claude kodu mesajda
  verir, "Open as proposal" ile aynı yere yazılır).

## 7. "Setup" denetimi — bir şey eksikse ne olur?

IDE açılınca sağ üstteki **Setup** butonu ortamı kontrol eder ve sonucu gösterir:
`Setup ✓` (her şey tamam), `Setup ⚠ n` (isteğe bağlı bir özellik kullanılamıyor),
`Setup ✗ n` (kurulu bir özellik çalışamıyor). Butona tıklayınca her madde için durum,
açıklama ve kopyalanabilir kurulum komutu görünür:

| Madde | Eksikse | Çözüm |
|---|---|---|
| SAP RFC SDK | SAP profilleri Fetch yapamaz; IDE yine açılır, Local mod çalışır | DLL'leri `main.exe`'nin yanına kopyala |
| Claude Code CLI | Claude sekmesi açılmaz | `winget install --id Anthropic.ClaudeCode -e`, sonra `claude` ile giriş |
| Python + MCP | Claude sohbet eder ama SAP araçlarını ve `write_proposal`'ı kullanamaz | Python 3.12 + `pip install -r requirements.txt` |
| Git | Push / Pull çalışmaz | `winget install --id Git.Git -e` |
| .env | GitHub senkronu kapalı (isteğe bağlı) | `.env` dosyası (bkz. 4. bölüm) |

Hiçbir eksik programın açılmasını engellemez. Pencere yalnızca gerçekten çalışması beklenen
bir şey eksikse (ör. SAP profili var ama SDK yok) kendiliğinden açılır; alttaki kutuyla bunu
kapatabilirsin.
