# ZH PC Cleaner — Windows version

A Windows port of ZH MacCleaner. Same UI + license system; Windows internals.

## What works on Windows
- **System Junk** — `%TEMP%`, Windows Temp, thumbnail/INetCache, Prefetch
- **Browser Caches** — Chrome, Edge, Brave, Firefox
- **Developer Junk** — npm, pip, NuGet, Yarn caches
- **Large Files** — Downloads/Desktop/Documents/Videos/Pictures (>100 MB) with **full path + Reveal in Explorer**
- **Duplicates** — identical-file finder
- **Uninstaller** — lists installed programs (registry) → runs the program's uninstaller + clears AppData leftovers
- **Maintenance** — Flush DNS · Clear Windows Update cache · Empty Recycle Bin · Disk Cleanup
- Deletions go to the **Recycle Bin** (recoverable)
- Same **Pro license** (`maccleaner` app key — one license, both platforms)

## Build (run on a Windows PC)
1. Install Python 3.10+ (python.org) — tick "Add to PATH".
2. (Optional) put an `icon.ico` in this folder.
3. Double-click **`build_win.bat`** (or run it in cmd).
4. Output: **`dist\ZH PC Cleaner.exe`** — a single self-contained .exe.

> Build must run on Windows — PyInstaller produces a .exe only on Windows (can't cross-build from Mac).

## Deploy (auto-update like the Mac app)
Upload to your server under **`public_html/pccleaner/`**:
| File | From |
|---|---|
| `ZH-PC-Cleaner.exe` | rename `dist\ZH PC Cleaner.exe` |
| `download.php` | `server/download.php` (serves newest .exe) |
| `version.json` | `server/version.json` (drives the update notification) |

The app checks `zhmotions.com/pccleaner/version.json`; bump its `version` for each release so users get the **update** message (same system as Mac).

## First run for buyers
Unsigned .exe → SmartScreen: **More info → Run anyway** (one time). Code-sign later to remove this.

## Notes
- Some maintenance (Update cache) needs **admin** — run the app as administrator for those.
- No "Full Disk Access" concept on Windows (that banner is Mac-only, disabled here).
