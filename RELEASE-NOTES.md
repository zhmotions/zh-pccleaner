# ZH PC Cleaner 1.1.1

## Cleans system junk now (admin)
- App requests Administrator on launch (UAC) → can clear Windows\Temp, Prefetch, and other locked
  system caches. Previously these were skipped without admin, so a clean could free ~0.
- Reminder: close Chrome / Edge / Adobe before cleaning — an open browser locks its cache (rebuilds it live).

## From 1.1.0 (carried forward)
- Installer auto-removes the old version (MSI major-upgrade).
- Honest delete/uninstall reporting (shows what couldn't be removed).
- Version shown in the app.

Requires Windows 10/11.
