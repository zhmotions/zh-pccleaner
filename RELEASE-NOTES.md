# ZH PC Cleaner 1.1.0

## Installer
- New install now AUTO-REMOVES the old version (MSI major-upgrade via stable upgrade_code + version bump). No more duplicate installs.
- Quit the app before installing (a running .exe locks its files).
- A copy left by a pre-1.1.0 build (no upgrade_code) must be removed once via Settings → Apps; every install after that upgrades cleanly.

## Reliability
- Move-to-Recycle-Bin now reports what it couldn't remove (in use / needs admin) instead of a false "done".
- Uninstaller, large-file trash, and duplicate delete all show honest counts ("⚠ N couldn't be moved").
- Cache clean says plainly when caches came back (an open Chrome/Edge/Adobe rebuilds them live) or files were locked.

## UI
- Version now shown in the header, sidebar, and About (v1.1.0).

Requires Windows 10/11.
