#!/usr/bin/env python3
"""
ZH PC Cleaner — a safe Windows cleaner (pro UI, ZH Motions theme)

Cleans: System junk (caches/logs), Browser caches, Dev junk, Large/old files.
Safety:
  • Only a hard-coded whitelist of known-safe user paths.
  • Cache/log contents are deleted (OS/apps regenerate them).
  • Your own files (large-file finder) move to the Recycle Bin (recoverable).
  • Auto-scans on launch and shows sizes BEFORE you clean.
"""

import os, sys, threading, queue, time, subprocess, hashlib, json, shutil
import ctypes
try:
    import winreg
except Exception:
    winreg = None
import urllib.request, urllib.parse, ssl

# SSL context with a real CA bundle. A PyInstaller .app on a fresh client Mac often
# can't find the system CA certs → urlopen raises SSLCertVerificationError →
# "Couldn't reach the license server" even though the network is fine. Bundle certifi
# so verification works on every Mac; fall back to the default context if unavailable.
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    try:
        SSL_CTX = ssl.create_default_context()
    except Exception:
        SSL_CTX = None
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

HOME = Path.home()
if getattr(sys, "frozen", False):                       # PyInstaller bundle
    APP_DIR = Path(getattr(sys, "_MEIPASS", Path.cwd()))
elif "__file__" in globals():
    APP_DIR = Path(__file__).resolve().parent
else:
    APP_DIR = Path.cwd()

APP_VERSION = "1.1.3"   # Windows build — bump EVERY release so the MSI major-upgrade removes the old one
SITE        = "https://www.zhmotions.com"
WIN_DL      = "https://zhmotions.com/pccleaner/download"
# Same update system as ZH Downloader: zhmotions.com FIRST, GitHub as fallback.
#   version.json -> {"version":"1.1","download_url":"https://.../ZH-MacCleaner.dmg","notes":"..."}
UPDATE_SOURCES = [
    ("zhmotions", "https://zhmotions.com/pccleaner/version.json", "zhm"),
    # Hostinger firewall 403s Python's TLS fingerprint (curl passes, urllib doesn't) -> the direct
    # check fails for many clients. GitHub Releases is the clean fallback (tag_name = version).
    ("github", "https://api.github.com/repos/zhmotions/zh-pccleaner/releases/latest", "gh"),
]

# ── Licensing: free app, Pro features unlocked by a key (self-hosted) ──
LICENSE_URL   = "https://zhmotions.com/api/license/verify"   # non-www + no .php (server strips it; redirects drop POST body)
LIC_FILE      = HOME/".config/zhmaccleaner/license.json"
PRO_FEATURES  = {"uninstall", "dupes", "maint"}     # locked until Pro
# ── In-app review prompt (after a few days of use) ──
REVIEW_URL    = "https://zhmotions.com/api.php?action=review_submit"
# Hostinger's firewall serves flagged IPs a 403 HTML challenge on direct POSTs (the "network problem"
# reports) — the Cloudflare Worker relay forwards from a clean IP, same as SMS/STT.
REVIEW_URL_FALLBACK = "https://api-relay-2.zhmotionspanel.workers.dev/api.php?action=review_submit"
REVIEW_FILE   = HOME/".config/zhpccleaner/review.json"
REVIEW_AFTER_DAYS = 3
APP_SLUG      = "pccleaner"
GRACE_DAYS    = 14                                  # offline grace after last good check

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")   # Cloudflare blocks bot UAs

def device_id():
    uid = "unknown"
    try:
        if winreg:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Microsoft\\Cryptography")
            uid = winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        uid = "unknown"
    return hashlib.sha256(uid.encode()).hexdigest()[:16]

# ── Monochromatic palette — every tone is a shade of ONE maroon hue ──
C = {
    "BG":"#f7f1f2", "SIDEBAR":"#ead9dd", "HEADER":"#fdfbfb",
    "SURF":"#ffffff", "SURF2":"#ecdade", "BORDER":"#dfc8cd",
    "MAROON":"#7A1F2B", "MAROON2":"#9c2a3a",
    "GOLD":"#7A1F2B", "GOLD2":"#9c2a3a",   # accents = maroon
    "TEXT":"#2c1014", "MUTED":"#9a767c",   # darkest maroon / muted maroon
    "GREEN":"#7A1F2B", "RED":"#9c2a3a",
}
UIFONT = "Segoe UI"
MONO   = "Consolas"

# ── Helpers ─────────────────────────────────────────────────────────────
def human(n):
    n = float(n)
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024: return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def dir_size(path):
    p = str(path)
    if not os.path.exists(p): return 0
    total = 0
    try:
        for root, _, files in os.walk(p, onerror=lambda e: None):
            for fn in files:
                try: total += os.path.getsize(os.path.join(root, fn))
                except OSError: pass
    except Exception:
        pass
    return total

def move_to_trash(path):
    """Send a file/folder to the Windows Recycle Bin (recoverable). Returns True only if it's
    actually gone afterwards — so callers report what couldn't be removed (in use / permission)
    instead of a false success."""
    p = os.path.abspath(str(path))
    try:
        from ctypes import wintypes
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        FO_DELETE=3; FOF_ALLOWUNDO=0x40; FOF_NOCONFIRMATION=0x10; FOF_SILENT=0x4; FOF_NOERRORUI=0x400
        op = SHFILEOPSTRUCTW(None, FO_DELETE, p + "\x00\x00", None,
                             FOF_ALLOWUNDO|FOF_NOCONFIRMATION|FOF_SILENT|FOF_NOERRORUI, False, None, None)
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception:
        pass
    return not os.path.exists(p)

# Temp/cache subfolders we must NEVER wipe — they hold Adobe CEP extension data
# (localStorage under Temp\cep_cache), where panels like ZH Script Studio keep their
# license/activation. Wiping TEMP blindly logs the user out of every CEP extension.
# NOTE: "Adobe" is deliberately NOT here — Adobe-named cache/temp folders are pure
# regenerable render cache and hold no license. The CEP license lives in the separate
# cep_cache / CSXS / com.adobe.cep entries, which we keep.
CACHE_PROTECT = {"cep_cache", "CSXS", "com.adobe.cep", "cep"}

def clear_contents(path, protect=None):
    """Delete a dir's contents (keep the dir). Locked/in-use files are skipped, never freezes.
    `protect` = top-level entry names to KEEP (e.g. Adobe CEP data → preserves licenses)."""
    p = str(path)
    if not os.path.isdir(p):
        return
    for e in os.listdir(p):
        if protect and e in protect:
            continue
        fp = os.path.join(p, e)
        try:
            if os.path.isdir(fp) and not os.path.islink(fp):
                shutil.rmtree(fp, ignore_errors=True)
            else:
                os.remove(fp)
        except Exception:
            pass

# ── App uninstaller (Windows: registry Uninstall entries) ───────────────
LEFTOVER_DIRS = [
    Path(os.environ.get("LOCALAPPDATA", HOME/"AppData/Local")),
    Path(os.environ.get("APPDATA", HOME/"AppData/Roaming")),
    Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")),
]

def list_apps():
    """Installed programs from the registry → (DisplayName, UninstallString)."""
    apps = {}
    if not winreg: return []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall")]
    for hive, path in roots:
        try: k = winreg.OpenKey(hive, path)
        except OSError: continue
        try: n = winreg.QueryInfoKey(k)[0]
        except OSError: n = 0
        for i in range(n):
            try:
                sk = winreg.OpenKey(k, winreg.EnumKey(k, i))
                name = winreg.QueryValueEx(sk, "DisplayName")[0]
                try: unstr = winreg.QueryValueEx(sk, "UninstallString")[0]
                except OSError: unstr = ""
                try:
                    if winreg.QueryValueEx(sk, "SystemComponent")[0]: continue
                except OSError: pass
                if name and unstr and name.lower() not in apps:
                    apps[name.lower()] = (name, unstr)
            except OSError:
                pass
    return sorted(apps.values(), key=lambda a: a[0].lower())

def bundle_id(app_path):
    return ""   # n/a on Windows

# Vendor/system folders we must NEVER delete as "leftovers" (a loose substring match used to
# nuke these when uninstalling an unrelated app — e.g. Adobe/Premiere data under AppData).
PROTECTED = ("adobe", "microsoft", "google", "apple", "packages", "temp", "windows",
             "commonfiles", "comms", "connecteddevices", "mozilla", "nvidia", "intel")

def app_leftovers(app_name, app_path):
    """Find leftover AppData/ProgramData folders that belong to THIS app only."""
    name_l = app_name.lower().replace(" ", "")
    hits = []
    if len(name_l) < 4:                      # too-short names match everything → skip
        return hits
    for d in LEFTOVER_DIRS:
        if not d.exists(): continue
        try:
            for e in os.listdir(d):
                el = e.lower().replace(" ", "")
                if any(el.startswith(p) for p in PROTECTED):
                    continue                  # protect vendor/system folders
                # name-prefix match only — no loose substring (entry must BE / start with the app name)
                if el == name_l or el.startswith(name_l + "-") or el.startswith(name_l + "_") or el.startswith(name_l + "."):
                    hits.append(d/e)
        except OSError:
            pass
    return hits

# ── Duplicate finder ────────────────────────────────────────────────────
def _quickhash(fp):
    h = hashlib.md5()
    try:
        with open(fp, "rb") as f:
            h.update(f.read(65536))           # first 64 KB — fast, good enough
    except OSError:
        return None
    return h.hexdigest()

def find_duplicates(dirs, min_size=1024*1024):
    by_size = {}
    for d in dirs:
        if not os.path.isdir(d): continue
        for root, _, files in os.walk(d, onerror=lambda e: None):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if os.path.islink(fp): continue
                    sz = os.path.getsize(fp)
                    if sz >= min_size: by_size.setdefault(sz, []).append(fp)
                except OSError:
                    pass
    groups = []
    for sz, paths in by_size.items():
        if len(paths) < 2: continue
        by_hash = {}
        for p in paths:
            hh = _quickhash(p)
            if hh: by_hash.setdefault(hh, []).append(p)
        for hh, ps in by_hash.items():
            if len(ps) > 1: groups.append((sz, ps))
    groups.sort(key=lambda g: g[0]*len(g[1]), reverse=True)
    return groups

def fda_granted():
    return True   # Windows has no Full Disk Access concept — banner never needed

def free_mem_bytes():
    """Available physical memory via GlobalMemoryStatusEx."""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return int(m.ullAvailPhys)
    except Exception:
        return 0

def run_admin(shell_cmd):
    """Run a Windows command (best-effort). Tasks needing elevation skip silently if not admin."""
    try:
        r = subprocess.run(["cmd", "/c", shell_cmd], capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except Exception as e:
        return False, str(e)

# ── Clean categories ────────────────────────────────────────────────────
LOCAL   = Path(os.environ.get("LOCALAPPDATA", HOME/"AppData/Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME/"AppData/Roaming"))
TEMP    = Path(os.environ.get("TEMP", LOCAL/"Temp"))
WINDIR  = Path(os.environ.get("WINDIR", "C:/Windows"))

CATEGORIES = {
    "system":  ("🧹", "System Junk", "temp · logs · thumbnails",
                [TEMP, WINDIR/"Temp", LOCAL/"Microsoft/Windows/Explorer",
                 LOCAL/"Microsoft/Windows/INetCache", WINDIR/"Prefetch"]),
    "browser": ("🌐", "Browser Caches", "Chrome · Edge · Firefox · Brave",
                [LOCAL/"Google/Chrome/User Data/Default/Cache",
                 LOCAL/"Google/Chrome/User Data/Default/Code Cache",
                 LOCAL/"Google/Chrome/User Data/Default/GPUCache",
                 LOCAL/"Microsoft/Edge/User Data/Default/Cache",
                 LOCAL/"Microsoft/Edge/User Data/Default/Code Cache",
                 LOCAL/"BraveSoftware/Brave-Browser/User Data/Default/Cache",
                 ROAMING/"Mozilla/Firefox/Profiles"]),
    "dev":     ("⚙️", "Developer Junk", "npm · pip · nuget · yarn",
                [ROAMING/"npm-cache", LOCAL/"pip/Cache", HOME/".nuget/packages",
                 LOCAL/"Yarn/Cache", LOCAL/"NuGet/Cache"]),
    # Adobe Premiere/AE media cache (cfa/pek/peak) — big space, regenerates. Does NOT touch
    # Adobe CEP extension data (licenses) — that lives under Adobe/CEP, not Common/Media Cache.
    "adobe":   ("🎬", "Adobe Media Cache", "Premiere · After Effects",
                [ROAMING/"Adobe/Common/Media Cache Files",
                 ROAMING/"Adobe/Common/Media Cache",
                 ROAMING/"Adobe/Common/Peak Files"]),
}
SCAN_DIRS = [HOME/"Downloads", HOME/"Desktop", HOME/"Documents", HOME/"Videos", HOME/"Pictures"]
BIG_THRESHOLD = 100 * 1024 * 1024

# ring segment + card accent per category — monochromatic maroon shades
SEG = {"system":"#5E1622", "browser":"#8A2A38", "dev":"#B5606A", "adobe":"#C77B4A"}

CARD_HELP = {
    "system":  "App caches & temp files Windows rebuilds automatically. Safe to delete — frees space, apps just re-cache.",
    "browser": "Cached web data for Chrome/Safari/Firefox. You stay logged in; pages just re-download once.",
    "dev":     "Build caches from npm, pip, Homebrew, Xcode. Safe — they regenerate on next build/install.",
    "adobe":   "Premiere/After Effects media cache (cfa/pek/peak files). Safe to clear — Adobe rebuilds them on next preview. Does NOT remove your extension licenses or settings.",
}


# ── hover tooltip ───────────────────────────────────────────────────────
class Tip:
    def __init__(self, widget, text):
        self.w, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
    def _show(self, _e):
        if self.tip or not self.text: return
        x = self.w.winfo_rootx() + 24
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tip = tk.Toplevel(self.w); self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#2c1014", fg="#ffffff", font=(UIFONT, 10),
                 padx=9, pady=6, justify="left", wraplength=280).pack()
    def _hide(self, _e):
        if self.tip: self.tip.destroy(); self.tip = None


# ════════════════════════════════════════════════════════════════════════
class Cleaner(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ZH PC Cleaner")
        self.geometry("880x760"); self.resizable(False, False)   # fixed size — fits all buttons
        self.configure(bg=C["BG"])
        self.q = queue.Queue()
        self.sizes = {}            # key -> bytes
        self.vars = {}             # key -> BooleanVar
        self.size_lbls = {}        # key -> Label
        self.big_files = []
        self.big_vars = {}
        self.busy = False
        self.logo_img = None
        self.nav_btns = {}
        self.views = {}

        self.lic = {"key": "", "plan": "free", "valid": False, "checked": 0}
        self._load_license()

        self._build()
        self.after(80, self._pump)
        self.after(300, self.scan_all)         # auto-scan on launch
        self.after(2500, lambda: self.check_updates(silent=True))  # quiet update check
        self.after(1500, self._reverify_license)   # refresh Pro status online
        self.after(4000, self._maybe_review)       # ask for a review after a few days
        self._trash_size()

    # ── UI ──
    def _build(self):
        # Header: Z-mark icon + app name
        head = tk.Frame(self, bg=C["HEADER"], height=80); head.pack(fill="x"); head.pack_propagate(False)
        icon_path = APP_DIR/"assets"/"icon.png"
        if icon_path.exists():
            try:  # Tk 9.0 loads PNG natively
                img = tk.PhotoImage(file=str(icon_path))
                self.logo_img = img.subsample(max(1, img.height() // 44), max(1, img.height() // 44))
                tk.Label(head, image=self.logo_img, bg=C["HEADER"]).pack(side="left", padx=(18,12), pady=16)
            except Exception:
                pass
        name = tk.Frame(head, bg=C["HEADER"]); name.pack(side="left")
        titlerow = tk.Frame(name, bg=C["HEADER"]); titlerow.pack(anchor="w")
        tk.Label(titlerow, text="ZH PC Cleaner", bg=C["HEADER"], fg=C["MAROON"],
                 font=(UIFONT, 19, "bold")).pack(side="left")
        tk.Label(titlerow, text=f"  v{APP_VERSION}", bg=C["HEADER"], fg=C["MUTED"],
                 font=(UIFONT, 11, "bold")).pack(side="left", pady=(6,0))
        tk.Label(name, text="keep your PC clean", bg=C["HEADER"], fg=C["MUTED"],
                 font=(UIFONT, 10)).pack(anchor="w", pady=(1,0))
        # subtle bottom divider
        tk.Frame(self, bg=C["BORDER"], height=1).pack(fill="x")

        body = tk.Frame(self, bg=C["BG"]); body.pack(fill="both", expand=True)

        # Sidebar
        side = tk.Frame(body, bg=C["SIDEBAR"], width=180); side.pack(side="left", fill="y"); side.pack_propagate(False)
        self.active_view = None
        nav = [("cleanup","Cleanup","🧹"), ("large","Large Files","📦"),
               ("uninstall","Uninstaller","🗑️"), ("dupes","Duplicates","👯"),
               ("maint","Maintenance","🛠"), ("license","Pro","⭐"), ("help","Help & About","ℹ️")]
        for key, label, ico in nav:
            b = tk.Label(side, text=f"   {ico}   {label}", bg=C["SIDEBAR"], fg=C["TEXT"],
                         font=(UIFONT, 13), anchor="w", cursor="hand2", padx=12, pady=11)
            b.pack(fill="x", padx=8, pady=2)
            b.bind("<Button-1>", lambda e,k=key: self.show_view(k))
            b.bind("<Enter>", lambda e,k=key,w=b: (w.config(bg="#e3d0d4") if k!=self.active_view else None))
            b.bind("<Leave>", lambda e,k=key,w=b: (w.config(bg=C["SIDEBAR"]) if k!=self.active_view else None))
            self.nav_btns[key] = b
        tk.Label(side, text=f"v{APP_VERSION} · safe mode", bg=C["SIDEBAR"], fg=C["BORDER"],
                 font=(UIFONT, 9)).pack(side="bottom", pady=12)

        # Content area
        self.content = tk.Frame(body, bg=C["BG"]); self.content.pack(side="left", fill="both", expand=True)
        self._build_cleanup()
        self._build_large()
        self._build_uninstaller()
        self._build_duplicates()
        self._build_maintenance()
        self._build_license()
        self._build_help()

        # Status bar
        self.status = tk.Label(self, text="Scanning…", bg=C["HEADER"], fg=C["MUTED"],
                               anchor="w", font=(UIFONT, 10), padx=16, pady=6)
        self.status.pack(fill="x", side="bottom")

        self.show_view("cleanup")

    # ── Cleanup view ──
    def _build_cleanup(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["cleanup"] = v

        # Full Disk Access banner (only if not granted)
        if not fda_granted():
            ban = tk.Frame(v, bg=C["SURF2"], highlightbackground=C["MAROON"], highlightthickness=1)
            ban.pack(fill="x", padx=22, pady=(12,0))
            ban.columnconfigure(1, weight=1)
            tk.Label(ban, text="🔒", bg=C["SURF2"], font=(UIFONT, 18)
                     ).grid(row=0, column=0, rowspan=2, padx=(12,6), pady=10)
            tk.Label(ban, text="Enable Full Disk Access", bg=C["SURF2"], fg=C["MAROON"], anchor="w",
                     font=(UIFONT, 12, "bold")).grid(row=0, column=1, sticky="w", pady=(10,0))
            tk.Label(ban, text="Lets ZH PC Cleaner read & clear all caches.", bg=C["SURF2"],
                     fg=C["MUTED"], anchor="w", font=(UIFONT, 10)).grid(row=1, column=1, sticky="w", pady=(0,10))
            tk.Button(ban, text="Open Settings", command=self.open_fda, highlightbackground=C["SURF2"],
                      fg=C["MAROON"], relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
                      font=(UIFONT, 11, "bold")).grid(row=0, column=2, rowspan=2, padx=12)

        # Gauge
        top = tk.Frame(v, bg=C["BG"]); top.pack(fill="x", pady=(12,4))
        self.gauge = tk.Canvas(top, width=176, height=176, bg=C["BG"], highlightthickness=0)
        self.gauge.pack()
        self._draw_gauge()

        # Category cards
        mid = tk.Frame(v, bg=C["BG"]); mid.pack(fill="both", expand=True, padx=22)
        for key,(ico,name,sub,paths) in CATEGORIES.items():
            card = tk.Frame(mid, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
            card.pack(fill="x", pady=5); card.columnconfigure(3, weight=1)
            tk.Frame(card, bg=SEG[key], width=4).grid(row=0, column=0, rowspan=2, sticky="ns")  # accent bar
            var = tk.BooleanVar(value=True); self.vars[key] = var
            tk.Checkbutton(card, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                           activebackground=C["SURF"], bd=0, highlightthickness=0
                           ).grid(row=0, column=1, rowspan=2, padx=(12,4), pady=14)
            tk.Label(card, text=ico, bg=C["SURF"], font=(UIFONT, 18)
                     ).grid(row=0, column=2, rowspan=2, padx=6)
            tk.Label(card, text=name, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 14, "bold")).grid(row=0, column=3, sticky="w", pady=(12,0))
            tk.Label(card, text=sub, bg=C["SURF"], fg=C["MUTED"], anchor="w",
                     font=(UIFONT, 10)).grid(row=1, column=3, sticky="w", pady=(0,12))
            szl = tk.Label(card, text="…", bg=C["SURF"], fg=C["GOLD"],
                           font=(MONO, 15, "bold")); szl.grid(row=0, column=4, rowspan=2, padx=20)
            self.size_lbls[key] = szl
            Tip(card, CARD_HELP[key])
            for wdg in [card] + list(card.winfo_children()):
                wdg.bind("<Enter>", lambda e,c=card: c.config(highlightbackground=C["MAROON2"]))
                wdg.bind("<Leave>", lambda e,c=card: c.config(highlightbackground=C["BORDER"]))

        self.trash_lbl = tk.Label(mid, text="🗑  Trash: …", bg=C["BG"], fg=C["MUTED"],
                                  font=(UIFONT, 11)); self.trash_lbl.pack(anchor="w", pady=(8,0))

        # Buttons
        bar = tk.Frame(v, bg=C["BG"]); bar.pack(fill="x", padx=22, pady=14, side="bottom")
        self.rescan_btn = self._btn(bar, "↻  Rescan", self.scan_all, "ghost"); self.rescan_btn.pack(side="left")
        self.clean_btn  = self._btn(bar, "✦  Clean Selected", self.clean_sel, "gold"); self.clean_btn.pack(side="left", padx=8)
        self.trash_btn  = self._btn(bar, "🗑  Empty Trash", self.empty_trash, "ghost"); self.trash_btn.pack(side="right")

    # ── Large files view ──
    def _build_large(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["large"] = v
        top = tk.Frame(v, bg=C["BG"]); top.pack(fill="x", padx=22, pady=(18,8))
        tk.Label(top, text="Files > 100 MB in Downloads · Desktop · Documents · Movies",
                 bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 11)).pack(side="left")
        self.find_btn = self._btn(top, "🔍  Find", self.scan_big, "gold"); self.find_btn.pack(side="right")

        wrap = tk.Frame(v, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=22, pady=6)
        self.bcanvas = tk.Canvas(wrap, bg=C["SURF"], highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.bcanvas.yview)
        self.binner = tk.Frame(self.bcanvas, bg=C["SURF"])
        self.binner.bind("<Configure>", lambda e: self.bcanvas.configure(scrollregion=self.bcanvas.bbox("all")))
        self.bwin = self.bcanvas.create_window((0,0), window=self.binner, anchor="nw")
        self.bcanvas.bind("<Configure>", lambda e: self.bcanvas.itemconfig(self.bwin, width=e.width))
        self.bcanvas.configure(yscrollcommand=sb.set)
        self.bcanvas.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.trash_sel_btn = self._btn(v, "🗑  Move Selected to Trash", self.trash_big, "gold")
        self.trash_sel_btn.pack(anchor="e", padx=22, pady=10)

    def _btn(self, parent, text, cmd, kind="gold"):
        styles = {"gold":(C["GOLD"], "#ffffff"), "ghost":(C["SURF2"], C["TEXT"]),
                  "danger":(C["RED"], "#fff")}
        bg, fg = styles[kind]
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat", bd=0,
                         padx=16, pady=9, cursor="hand2", activebackground=C["GOLD2"], activeforeground="#ffffff",
                         font=(UIFONT, 12, "bold"))

    def open_fda(self):
        pass   # Windows has no Full Disk Access screen

    def _draw_gauge(self, frac=None, total=None):
        g = self.gauge; g.delete("all")
        x0,y0,x1,y1 = 16,16,160,160; cx,cy = 88,88
        g.create_oval(x0,y0,x1,y1, outline=C["SURF2"], width=10)   # track
        real = sum(self.sizes.values())
        if frac is None:                      # final state — segmented ring
            if real > 0:
                start = 90.0
                for k in CATEGORIES:
                    val = self.sizes.get(k, 0)
                    if val <= 0: continue
                    g.create_arc(x0,y0,x1,y1, start=start, extent=-359.0*(val/real),
                                 style="arc", outline=SEG[k], width=10)
                    start += -359.0*(val/real)
            shown = real
        else:                                 # animating — single growing arc
            if frac > 0:
                g.create_arc(x0,y0,x1,y1, start=90, extent=-359.0*min(frac,1.0),
                             style="arc", outline=C["MAROON"], width=10)
            shown = real if total is None else total
        txt = human(shown) if (real > 0 or total is not None) else "—"
        fs = 23 if len(txt) <= 7 else (19 if len(txt) <= 9 else 16)
        g.create_text(cx, cy-11, text=txt, fill=C["TEXT"], font=(UIFONT, fs, "bold"))
        g.create_text(cx, cy+17, text="RECLAIMABLE", fill=C["MUTED"], font=(UIFONT, 9, "bold"))

    def _animate_gauge(self):
        target = sum(self.sizes.values())
        if target <= 0:
            self._draw_gauge(); return
        steps = 24
        def step(i=[0]):
            i[0] += 1
            e = 1 - (1 - i[0]/steps)**3          # ease-out
            if i[0] >= steps:
                self._draw_gauge()                # settle to real segmented ring
            else:
                self._draw_gauge(frac=e, total=target*e)
                self.after(20, step)
        step()

    def show_view(self, name):
        # Pro gate
        if name in PRO_FEATURES and not self.is_pro():
            feat = {"uninstall":"App Uninstaller","dupes":"Duplicate Finder","maint":"Maintenance"}.get(name,"This")
            if hasattr(self, "lic_ctx"):
                self.lic_ctx.config(text=f"🔒  {feat} is a Pro feature — activate a license to unlock it.")
            self._refresh_license_ui()
            name = "license"
        elif hasattr(self, "lic_ctx"):
            self.lic_ctx.config(text="")
        self.active_view = name
        for v in self.views.values(): v.pack_forget()
        self.views[name].pack(fill="both", expand=True)
        if name == "uninstall": self.load_apps()
        for k,b in self.nav_btns.items():
            if k == name: b.config(bg=C["MAROON"], fg="#ffffff", font=(UIFONT, 13, "bold"))
            else:         b.config(bg=C["SIDEBAR"], fg=C["TEXT"], font=(UIFONT, 13))

    # ── queue pump ──
    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if   kind == "status": self.status.config(text=payload)
                elif kind == "size":   self._set_size(*payload)
                elif kind == "gauge":  self._draw_gauge()
                elif kind == "trash":  self.trash_lbl.config(text=payload)
                elif kind == "big":    self._render_big(payload)
                elif kind == "busy":   self._set_busy(payload)
                elif kind == "apps":   self._render_apps(payload)
                elif kind == "uninstall_confirm": self._confirm_uninstall(*payload)
                elif kind == "dupes":  self._render_dupes(payload)
                elif kind == "rescan_dupes": self.scan_dupes()
                elif kind == "update": self._show_update(*payload)
                elif kind == "gauge_anim": self._animate_gauge()
                elif kind == "maint_done": self._maint_done(*payload)
                elif kind == "clean_done":
                    messagebox.showinfo("ZH PC Cleaner", f"✅ Cleanup complete.\n\nFreed about {human(payload)}.")
                elif kind == "license_changed": self._refresh_license_ui()
                elif kind == "license_result":
                    ok, msg = payload
                    (messagebox.showinfo if ok else messagebox.showwarning)("ZH PC Cleaner — License", msg)
                    self._refresh_license_ui()
                    if ok and self.active_view == "license": self.show_view("cleanup")
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _set_busy(self, b):
        self.busy = b
        st = "disabled" if b else "normal"
        for x in ("rescan_btn","clean_btn","trash_btn","find_btn","trash_sel_btn"):
            try: getattr(self, x).config(state=st)
            except Exception: pass

    def _set_size(self, key, n):
        self.sizes[key] = n
        self.size_lbls[key].config(text=human(n))
        total = sum(self.sizes.values())
        self._draw_gauge()

    def _trash_size(self):
        threading.Thread(target=lambda: self.q.put(("trash", f"🗑  Recycle Bin: {human(dir_size(HOME/'.Trash'))}")),
                         daemon=True).start()

    # ── scan ──
    def scan_all(self):
        if self.busy: return
        for l in self.size_lbls.values(): l.config(text="…")
        self.q.put(("busy", True)); self.q.put(("status","Scanning caches…"))
        def run():
            for key,(ico,name,sub,paths) in CATEGORIES.items():
                tot = sum(dir_size(p) for p in paths if p.exists())
                self.q.put(("size",(key,tot)))
            self.q.put(("gauge_anim", None))     # animated reveal
            self.q.put(("status","Scan complete. Review sizes, then Clean Selected."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def clean_sel(self):
        if self.busy: return
        picks = [k for k,v in self.vars.items() if v.get()]
        if not picks: messagebox.showinfo("ZH Cleaner","Nothing selected."); return
        est = sum(self.sizes.get(k,0) for k in picks)
        names = "\n".join("• "+CATEGORIES[k][1] for k in picks)
        if not messagebox.askyesno("Clean these?",
            f"Delete cache/log contents for:\n\n{names}\n\n≈ {human(est)} freed. "
            f"These regenerate automatically.\n\nContinue?"): return
        self.q.put(("busy", True))
        def run():
            freed = 0
            remaining_tot = 0
            try:
                for k in picks:
                    self.q.put(("status", f"Cleaning {CATEGORIES[k][1]}…"))   # live per-category
                    before = sum(dir_size(p) for p in CATEGORIES[k][3] if p.exists())
                    for p in CATEGORIES[k][3]:
                        # System Junk clears TEMP → protect Adobe CEP extension data
                        # (licenses in Temp\cep_cache) from being wiped. Others clear fully.
                        if p.exists(): clear_contents(p, protect=CACHE_PROTECT if k == "system" else None)
                    # Re-measure ACTUAL remaining — never report a fake "0".
                    # What stays = protected Adobe data, files locked by running apps,
                    # or cache an open app rebuilt instantly. Honest numbers only.
                    after = sum(dir_size(p) for p in CATEGORIES[k][3] if p.exists())
                    freed += max(0, before - after)
                    remaining_tot += after
                    self.q.put(("size", (k, after)))
            except Exception as e:
                self.q.put(("status", f"⚠ Clean error: {e}"))
            finally:                                                          # ALWAYS finish
                if remaining_tot > 5 * 1024 * 1024:   # >5 MB still there → explain why
                    if freed < remaining_tot * 0.2:
                        # Barely anything went down → an open app (Chrome/Edge/Adobe) rebuilds its cache
                        # the instant it's deleted, or the files are locked/in use. Tell the user plainly.
                        msg = (f"⚠ Freed {human(freed)} — most of it came back. Close Chrome / Edge / "
                               f"Adobe (they rebuild cache live), then re-clean. Locked files are skipped.")
                    else:
                        msg = (f"✅ Freed {human(freed)}. {human(remaining_tot)} still in use — "
                               f"close Chrome/Edge/Adobe (they rebuild cache live) & re-clean. "
                               f"Adobe extension data is protected on purpose.")
                else:
                    msg = f"✅ Cleaned. Freed {human(freed)}."
                self.q.put(("status", msg))
                self.q.put(("clean_done", freed))
                self.q.put(("busy", False))
                self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    def empty_trash(self):
        if self.busy: return
        if not messagebox.askyesno("Empty Recycle Bin","Permanently empty the Recycle Bin?"): return
        self.q.put(("busy", True))
        def run():
            try: ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x07)
            except Exception: pass
            self.q.put(("trash","🗑  Recycle Bin: 0 B")); self.q.put(("status","✅ Recycle Bin emptied."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    # ── large files ──
    def scan_big(self):
        if self.busy: return
        self.q.put(("busy", True)); self.q.put(("status","Finding large files…"))
        def run():
            dirs = [str(d) for d in SCAN_DIRS if d.exists()]; found = []
            if dirs:
                mb = BIG_THRESHOLD//(1024*1024)
                try:
                    out = subprocess.run(["find"]+dirs+["-type","f","-size",f"+{mb}M"],
                                         capture_output=True, text=True, timeout=120)
                    for fp in out.stdout.splitlines():
                        try:
                            if os.path.islink(fp): continue
                            st = os.stat(fp); found.append((fp, st.st_size, st.st_mtime))
                        except OSError: pass
                except Exception as e:
                    self.q.put(("status", f"find error: {e}"))
            found.sort(key=lambda x:x[1], reverse=True)
            self.q.put(("big", found[:200]))
            self.q.put(("status", f"Found {len(found)} file(s) > 100 MB."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def _render_big(self, found):
        for w in self.binner.winfo_children(): w.destroy()
        self.big_files = found; self.big_vars = {}
        if not found:
            tk.Label(self.binner, text="No files > 100 MB found.", bg=C["SURF"], fg=C["MUTED"],
                     font=(UIFONT, 12)).pack(pady=24); return
        now = time.time()
        for fp,sz,mt in found:
            row = tk.Frame(self.binner, bg=C["SURF"]); row.pack(fill="x", padx=6, pady=2)
            row.columnconfigure(1, weight=1)
            var = tk.BooleanVar(value=False); self.big_vars[fp] = var
            tk.Checkbutton(row, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                           activebackground=C["SURF"], bd=0, highlightthickness=0
                           ).grid(row=0, column=0, rowspan=2, sticky="w")
            nm = os.path.basename(fp); disp = (nm[:44]+"…") if len(nm)>45 else nm
            nml = tk.Label(row, text=disp, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 11, "bold")); nml.grid(row=0, column=1, sticky="w", padx=4)
            folder = os.path.dirname(fp); pdisp = ("…"+folder[-58:]) if len(folder)>59 else folder
            tk.Label(row, text=pdisp, bg=C["SURF"], fg=C["MUTED"], anchor="w",
                     font=(UIFONT, 9)).grid(row=1, column=1, sticky="w", padx=4)
            Tip(nml, fp)   # full path on hover — check before deleting
            tk.Label(row, text=f"{human(sz)} · {int((now-mt)/86400)}d", bg=C["SURF"], fg=C["GOLD"],
                     font=(MONO, 11)).grid(row=0, column=2, rowspan=2, sticky="e", padx=12)
            tk.Button(row, text="↗ Reveal", command=lambda p=fp: self.reveal_in_finder(p),
                      bg=C["SURF2"], fg=C["TEXT"], relief="flat", bd=0, padx=10, pady=3,
                      cursor="hand2", font=(UIFONT, 10), activebackground=C["MAROON2"]
                      ).grid(row=0, column=3, rowspan=2, padx=(4,8))

    def reveal_in_finder(self, path):
        """Open Finder with the file selected so the user can verify it before deleting."""
        if os.path.exists(path):
            subprocess.run(["explorer", "/select,", os.path.abspath(path)])
        else:
            self.q.put(("status", "File no longer exists."))

    def trash_big(self):
        if self.busy: return
        picks = [fp for fp,v in self.big_vars.items() if v.get()]
        if not picks: messagebox.showinfo("ZH Cleaner","No files selected."); return
        tot = sum(sz for fp,sz,_ in self.big_files if fp in picks)
        if not messagebox.askyesno("Move to Recycle Bin?",
            f"Move {len(picks)} file(s) ({human(tot)}) to the Recycle Bin?\nRecoverable from the Recycle Bin."): return
        self.q.put(("busy", True))
        def run():
            okp = [fp for fp in picks if move_to_trash(fp)]
            fail = len(picks) - len(okp)
            self.q.put(("big", [x for x in self.big_files if x[0] not in okp]))
            self.q.put(("status", f"✅ Moved {len(okp)} file(s) to the Recycle Bin."
                        + (f" ⚠ {fail} couldn't be moved (in use / permission)." if fail else "")))
            self.q.put(("busy", False)); self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    # ══ scrollable list helper ══
    def _scroller(self, parent):
        wrap = tk.Frame(parent, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=22, pady=6)
        cv = tk.Canvas(wrap, bg=C["SURF"], highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, bg=C["SURF"])
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        win = cv.create_window((0,0), window=inner, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfig(win, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        return inner

    def _title(self, parent, text, sub=""):
        f = tk.Frame(parent, bg=C["BG"]); f.pack(fill="x", padx=22, pady=(18,4))
        tk.Label(f, text=text, bg=C["BG"], fg=C["TEXT"], font=(UIFONT, 16, "bold")).pack(anchor="w")
        if sub: tk.Label(f, text=sub, bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10)).pack(anchor="w")
        return f

    # ══ UNINSTALLER ══
    def _build_uninstaller(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["uninstall"] = v
        self._title(v, "App Uninstaller", "Removes an app + all its leftover files")
        self.uapp_inner = self._scroller(v)
        tk.Label(self.uapp_inner, text="Loading apps…", bg=C["SURF"], fg=C["MUTED"],
                 font=(UIFONT, 11)).pack(pady=16)
        self._loaded_apps = False

    def load_apps(self):
        if getattr(self, "_loaded_apps", False) or self.busy: return
        self._loaded_apps = True
        def run():
            apps = list_apps()
            self.q.put(("apps", apps))
        threading.Thread(target=run, daemon=True).start()

    def _render_apps(self, apps):
        for w in self.uapp_inner.winfo_children(): w.destroy()
        for nm, path in apps:
            row = tk.Frame(self.uapp_inner, bg=C["SURF"]); row.pack(fill="x", padx=8, pady=1)
            row.columnconfigure(0, weight=1)
            tk.Label(row, text=nm, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 12)).grid(row=0, column=0, sticky="w", pady=4)
            tk.Button(row, text="Uninstall", command=lambda n=nm,p=path: self.uninstall_app(n,p),
                      bg=C["SURF2"], fg=C["MAROON"], relief="flat", bd=0, padx=10, pady=3,
                      cursor="hand2", font=(UIFONT, 10, "bold")).grid(row=0, column=1, padx=6)

    def uninstall_app(self, name, path):
        if self.busy: return
        self.q.put(("status", f"Scanning leftovers for {name}…"))
        def run():
            left = app_leftovers(name, path)
            tot = dir_size(path) + sum(dir_size(p) for p in left)
            self.q.put(("uninstall_confirm", (name, path, left, tot)))
        threading.Thread(target=run, daemon=True).start()

    def _confirm_uninstall(self, name, path, left, tot):
        msg = (f"Run the uninstaller for “{name}” and send {len(left)} leftover folder(s) to the Recycle Bin?\n\n"
               f"Leftovers ≈ {human(tot)}. The app's own uninstaller window may appear — follow it.")
        if not messagebox.askyesno("Uninstall app?", msg): return
        self.q.put(("busy", True)); self.q.put(("status", f"Uninstalling {name}…"))
        def run():
            try: subprocess.Popen(path, shell=True)   # path = the program's UninstallString
            except Exception: pass
            done = sum(1 for p in left if move_to_trash(str(p)))
            fail = len(left) - done
            self.q.put(("status", f"✅ {name}: uninstaller launched · {done}/{len(left)} leftover(s) → Recycle Bin."
                        + (f" ⚠ {fail} couldn't be removed (in use / needs admin)." if fail else "")))
            self.q.put(("busy", False)); self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    # ══ DUPLICATES ══
    def _build_duplicates(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["dupes"] = v
        f = self._title(v, "Duplicate Finder", "Finds identical files (>1 MB) in your folders")
        self.dupe_btn = self._btn(f, "🔍  Scan", self.scan_dupes, "gold"); self.dupe_btn.pack(side="right")
        self.dupe_inner = self._scroller(v)
        self.dupe_vars = {}
        self.del_dupe_btn = self._btn(v, "🗑  Delete Selected Copies", self.del_dupes, "gold")
        self.del_dupe_btn.pack(anchor="e", padx=22, pady=10)

    def scan_dupes(self):
        if self.busy: return
        self.q.put(("busy", True)); self.q.put(("status","Hashing files for duplicates…"))
        def run():
            groups = find_duplicates([str(d) for d in SCAN_DIRS])
            self.q.put(("dupes", groups))
            wasted = sum(sz*(len(ps)-1) for sz,ps in groups)
            self.q.put(("status", f"Found {len(groups)} duplicate set(s) · {human(wasted)} wasted."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def _render_dupes(self, groups):
        for w in self.dupe_inner.winfo_children(): w.destroy()
        self.dupe_vars = {}
        if not groups:
            tk.Label(self.dupe_inner, text="No duplicates found.", bg=C["SURF"], fg=C["MUTED"],
                     font=(UIFONT, 12)).pack(pady=20); return
        for sz, paths in groups:
            hdr = tk.Frame(self.dupe_inner, bg=C["SURF2"]); hdr.pack(fill="x", padx=4, pady=(8,0))
            tk.Label(hdr, text=f"{len(paths)} copies · {human(sz)} each", bg=C["SURF2"],
                     fg=C["MAROON"], anchor="w", font=(UIFONT, 11, "bold")).pack(anchor="w", padx=8, pady=3)
            for i, p in enumerate(paths):
                row = tk.Frame(self.dupe_inner, bg=C["SURF"]); row.pack(fill="x", padx=10)
                row.columnconfigure(1, weight=1)
                var = tk.BooleanVar(value=(i>0))   # keep first, mark extras
                self.dupe_vars[p] = var
                tk.Checkbutton(row, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                               activebackground=C["SURF"], bd=0, highlightthickness=0
                               ).grid(row=0, column=0, sticky="w")
                tag = "  (keep)" if i==0 else ""
                tk.Label(row, text=p.replace(str(HOME),"~")+tag, bg=C["SURF"],
                         fg=C["MUTED"] if i==0 else C["TEXT"], anchor="w",
                         font=(UIFONT, 10)).grid(row=0, column=1, sticky="w", padx=4)

    def del_dupes(self):
        if self.busy: return
        picks = [p for p,v in self.dupe_vars.items() if v.get()]
        if not picks: messagebox.showinfo("ZH PC Cleaner","No copies selected."); return
        if not messagebox.askyesno("Delete copies?",
            f"Move {len(picks)} duplicate file(s) to the Recycle Bin?\nRecoverable from the Recycle Bin."): return
        self.q.put(("busy", True))
        def run():
            ok = sum(1 for p in picks if move_to_trash(p))
            fail = len(picks) - ok
            self.q.put(("status", f"✅ {ok} duplicate(s) → Recycle Bin."
                        + (f" ⚠ {fail} couldn't be moved (in use / permission)." if fail else "")))
            self.q.put(("busy", False)); self._trash_size()
            self.q.put(("rescan_dupes", None))
        threading.Thread(target=run, daemon=True).start()

    # ══ MAINTENANCE ══
    def _build_maintenance(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["maint"] = v
        self._title(v, "Maintenance", "Quick system tune-ups (some need admin)")
        grid = tk.Frame(v, bg=C["BG"]); grid.pack(fill="x", padx=22, pady=8)
        tools = [
            ("🌐", "Flush DNS", "reset DNS cache",
             "Clears the DNS cache. Fixes websites that won't load or point to an old/wrong server.",
             lambda: self.maint("ipconfig /flushdns", "Flush DNS", admin=False)),
            ("🔄", "Clear Update Cache", "Windows Update junk",
             "Stops Windows Update, clears its download cache, restarts it. Frees space + fixes stuck updates. Needs admin.",
             lambda: self.maint('net stop wuauserv & rd /s /q "%WINDIR%\\SoftwareDistribution\\Download" & net start wuauserv', "Clear Update Cache", admin=True)),
            ("🗑", "Empty Recycle Bin", "delete for good",
             "Permanently empties the Recycle Bin to reclaim space.",
             lambda: self.maint('powershell -NoProfile -Command "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"', "Empty Recycle Bin", admin=False)),
            ("🧹", "Disk Cleanup", "Windows built-in",
             "Opens Windows Disk Cleanup for a deeper clean (old updates, system files).",
             lambda: self.maint("cleanmgr", "Disk Cleanup", admin=False)),
        ]
        for i,(ico,name,sub,tip,cmd) in enumerate(tools):
            card = tk.Frame(grid, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
            card.grid(row=i//2, column=i%2, sticky="nsew", padx=6, pady=6)
            grid.columnconfigure(i%2, weight=1)
            tk.Label(card, text=ico, bg=C["SURF"], font=(UIFONT, 22)).pack(pady=(12,2))
            tk.Label(card, text=name, bg=C["SURF"], fg=C["TEXT"], font=(UIFONT, 13, "bold")).pack()
            tk.Label(card, text=sub, bg=C["SURF"], fg=C["MUTED"], font=(UIFONT, 9)).pack()
            self._btn(card, "Run", cmd, "gold").pack(pady=10)
            Tip(card, tip)

    def maint(self, cmd, label, admin=True):
        if self.busy: return
        self.q.put(("busy", True)); self.q.put(("status", f"{label}…"))
        def run():
            before = free_mem_bytes() if label == "Free RAM" else None
            if admin: ok, out = run_admin(cmd)
            else:
                r = subprocess.run(["bash","-c",cmd], capture_output=True, text=True)
                ok, out = r.returncode == 0, (r.stderr or r.stdout).strip()
            if label == "Free RAM" and ok:
                after = free_mem_bytes()
                gained = after - (before or 0)
                detail = (f"✅ RAM freed.\n\nAvailable memory now: {human(after)}"
                          + (f"\nReclaimed: ~{human(gained)}" if gained > 0 else ""))
            elif ok:
                detail = f"✅ {label} completed successfully."
            else:
                low = (out or "").lower()
                if "cancel" in low or "-128" in low:
                    detail = "Cancelled — password not entered."
                else:
                    detail = f"⚠ {label} failed.\n\n{out[:160] or 'Unknown error.'}"
            self.q.put(("maint_done", (label, ok, detail)))
            self.q.put(("status", f"{'✅' if ok else '⚠'} {label}: {'done' if ok else 'failed'}"))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def _maint_done(self, label, ok, detail):
        (messagebox.showinfo if ok else messagebox.showwarning)("ZH PC Cleaner — " + label, detail)

    # ══ LICENSE / PRO ══
    def is_pro(self):
        return bool(self.lic.get("valid")) and self.lic.get("plan") == "pro"

    def _load_license(self):
        try:
            d = json.loads(LIC_FILE.read_text())
            self.lic.update(d)
            if self.lic.get("valid") and (time.time() - self.lic.get("checked", 0)) > GRACE_DAYS*86400:
                self.lic["valid"] = False      # grace expired, needs re-check
        except Exception:
            pass

    def _save_license(self):
        try:
            LIC_FILE.parent.mkdir(parents=True, exist_ok=True)
            LIC_FILE.write_text(json.dumps(self.lic))
        except Exception:
            pass

    def _verify_online(self, key):
        # Direct can 403: the host firewall blocks Python's TLS fingerprint / serves an HTML
        # challenge. Alternate direct -> Worker relay (clean IP, /api/ is on its allowlist).
        last_err = ""
        relay = LICENSE_URL.replace("https://zhmotions.com", "https://api-relay-2.zhmotionspanel.workers.dev")
        for attempt, url in enumerate((LICENSE_URL, relay, relay)):
            try:
                body = urllib.parse.urlencode({
                    "key": key, "app": "maccleaner", "device": device_id(), "v": APP_VERSION}).encode()
                req = urllib.request.Request(url, data=body, headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded"})   # so PHP fills $_POST
                raw = urllib.request.urlopen(req, timeout=20, context=SSL_CTX).read().decode()
                data = json.loads(raw)   # bot-challenge HTML -> JSONDecodeError -> retry
                return bool(data.get("valid")), (data.get("plan") or "pro"), (data.get("message") or "")
            except Exception as e:
                last_err = str(e)
                if attempt < 2:
                    time.sleep(1.2)
        return None, None, last_err           # None = couldn't reach after retries

    def _reverify_license(self):
        key = self.lic.get("key")
        if not key: return
        def run():
            ok, plan, _ = self._verify_online(key)
            if ok is None: return               # offline → keep cached within grace
            self.lic.update({"valid": bool(ok), "plan": plan or "free", "checked": time.time()})
            self._save_license(); self.q.put(("license_changed", None))
        threading.Thread(target=run, daemon=True).start()

    # ── In-app review prompt ────────────────────────────────────────────
    def _review_state(self):
        try: return json.loads(REVIEW_FILE.read_text())
        except Exception: return {}

    def _review_save(self, d):
        try:
            REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
            REVIEW_FILE.write_text(json.dumps(d))
        except Exception: pass

    def _maybe_review(self):
        st = self._review_state()
        now = time.time()
        if not st.get("first_run"):
            st["first_run"] = now; self._review_save(st); return    # start the clock on first launch
        if st.get("status") == "done": return
        if now < st.get("snooze_until", 0): return
        if now - st.get("first_run", now) < REVIEW_AFTER_DAYS * 86400: return
        try: self._show_review()
        except Exception: pass

    def _show_review(self):
        win = tk.Toplevel(self); win.title("Enjoying ZH PC Cleaner?")
        win.configure(bg=C["BG"]); win.resizable(False, False)
        try: win.transient(self)
        except Exception: pass
        W, H = 400, 360
        try:
            x = self.winfo_rootx() + (self.winfo_width() - W)//2
            y = self.winfo_rooty() + (self.winfo_height() - H)//3
            win.geometry(f"{W}x{H}+{max(0,x)}+{max(0,y)}")
        except Exception: win.geometry(f"{W}x{H}")

        tk.Label(win, text="Enjoying ZH PC Cleaner?", bg=C["BG"], fg=C["TEXT"],
                 font=(UIFONT, 16, "bold")).pack(anchor="w", padx=22, pady=(20, 2))
        tk.Label(win, text="Tap the stars and leave a quick review — it really helps.",
                 bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10), wraplength=356, justify="left").pack(anchor="w", padx=22)

        state = {"rating": 0}
        stars_row = tk.Frame(win, bg=C["BG"]); stars_row.pack(anchor="w", padx=20, pady=(12, 6))
        star_lbls = []
        def paint(n):
            for i, s in enumerate(star_lbls):
                s.config(fg=(C["GOLD"] if i < n else C["BORDER"]))
        def pick(n):
            state["rating"] = n; paint(n)
        for i in range(5):
            s = tk.Label(stars_row, text="★", bg=C["BG"], fg=C["BORDER"], font=(UIFONT, 30), cursor="hand2")
            s.pack(side="left", padx=2); s.bind("<Button-1>", lambda e, n=i+1: pick(n))
            star_lbls.append(s)

        tk.Label(win, text="Your name", bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 9)).pack(anchor="w", padx=22)
        name_e = tk.Entry(win, font=(UIFONT, 12), bg=C["SURF"], fg=C["TEXT"], relief="flat",
                          highlightthickness=1, highlightbackground=C["BORDER"], highlightcolor=C["GOLD"])
        name_e.pack(fill="x", padx=22, ipady=5, pady=(2, 8))
        cmt = tk.Text(win, height=3, font=(UIFONT, 11), bg=C["SURF"], fg=C["TEXT"], relief="flat",
                      highlightthickness=1, highlightbackground=C["BORDER"], highlightcolor=C["GOLD"], wrap="word")
        cmt.pack(fill="x", padx=22, pady=(0, 4))
        msg = tk.Label(win, text="", bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 9)); msg.pack(anchor="w", padx=22)

        btns = tk.Frame(win, bg=C["BG"]); btns.pack(fill="x", padx=20, pady=(6, 16), side="bottom")
        def later():
            st = self._review_state(); st["snooze_until"] = time.time() + 3*86400; self._review_save(st); win.destroy()
        def never():
            st = self._review_state(); st["status"] = "done"; self._review_save(st); win.destroy()
        def submit():
            name = name_e.get().strip(); comment = cmt.get("1.0", "end").strip(); rating = state["rating"]
            if rating < 1: msg.config(text="Please tap the stars to rate.", fg=C["GOLD"]); return
            if len(name) < 2: msg.config(text="Please enter your name.", fg=C["GOLD"]); return
            msg.config(text="Sending…", fg=C["MUTED"])
            def run():
                ok = False; err = "Couldn't send — check your internet and retry."
                body = urllib.parse.urlencode({"app": APP_SLUG, "name": name, "rating": rating, "comment": comment}).encode()
                # Direct first; if the host firewall serves its HTML challenge (JSON parse fails /
                # HTTPError), retry through the clean-IP Worker relay.
                for url in (REVIEW_URL, REVIEW_URL_FALLBACK):
                    try:
                        req = urllib.request.Request(url, data=body,
                              headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
                        data = json.loads(urllib.request.urlopen(req, timeout=15, context=SSL_CTX).read().decode())
                        ok = (data.get("status") == "success")
                        if not ok and data.get("message"): err = str(data.get("message"))   # real reason (e.g. already reviewed), not a fake network error
                        break                       # got a JSON answer (success OR rejection) → stop
                    except Exception:
                        ok = False                  # challenge/HTML/network → try the relay next
                def done():
                    if ok:
                        st = self._review_state(); st["status"] = "done"; self._review_save(st)
                        msg.config(text="Thank you! ★", fg=C["GOLD"]); win.after(900, win.destroy)
                    else:
                        msg.config(text=err, fg=C["RED"])
                self.after(0, done)
            threading.Thread(target=run, daemon=True).start()

        tk.Label(btns, text="Maybe later", bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10),
                 cursor="hand2").pack(side="left")
        tk.Label(btns, text="No thanks", bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10),
                 cursor="hand2").pack(side="left", padx=14)
        send = tk.Label(btns, text="  Post review  ", bg=C["GOLD"], fg="#fff", font=(UIFONT, 11, "bold"),
                        cursor="hand2", padx=6, pady=7); send.pack(side="right")
        send.bind("<Button-1>", lambda e: submit())
        btns.winfo_children()[0].bind("<Button-1>", lambda e: later())
        btns.winfo_children()[1].bind("<Button-1>", lambda e: never())

    def activate_license(self, key):
        key = key.strip()
        if not key: messagebox.showinfo("License", "Enter your license key first."); return
        self.q.put(("status", "Verifying license…"))
        def run():
            ok, plan, msg = self._verify_online(key)
            if ok is None:
                self.q.put(("license_result", (False, "Couldn't reach the license server. Check your internet.")))
            elif ok:
                self.lic.update({"key": key, "valid": True, "plan": plan or "pro", "checked": time.time()})
                self._save_license()
                self.q.put(("license_result", (True, "✅ Pro unlocked. Thank you for supporting ZH Motions!")))
            else:
                self.q.put(("license_result", (False, msg or "Invalid or inactive key.")))
        threading.Thread(target=run, daemon=True).start()

    def _build_license(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["license"] = v
        inner = self._scroller(v)
        self.lic_ctx = tk.Label(inner, text="", bg=C["SURF"], fg=C["MAROON"], anchor="w",
                                font=(UIFONT, 12, "bold"), wraplength=520, justify="left")
        self.lic_ctx.pack(fill="x", padx=14, pady=(14,0))
        tk.Label(inner, text="ZH PC Cleaner Pro", bg=C["SURF"], fg=C["TEXT"],
                 font=(UIFONT, 18, "bold")).pack(anchor="w", padx=14, pady=(8,2))
        self.lic_status = tk.Label(inner, text="", bg=C["SURF"], anchor="w", font=(UIFONT, 12, "bold"))
        self.lic_status.pack(fill="x", padx=14, pady=(0,8))

        tk.Label(inner, text="Pro unlocks:", bg=C["SURF"], fg=C["TEXT"], anchor="w",
                 font=(UIFONT, 12, "bold")).pack(fill="x", padx=14, pady=(6,2))
        for t in ("🗑️  App Uninstaller — remove apps + leftovers",
                  "👯  Duplicate Finder — reclaim wasted space",
                  "🛠  Maintenance — free RAM, flush DNS, reindex",
                  "↻  Priority updates from zhmotions.com"):
            tk.Label(inner, text="   "+t, bg=C["SURF"], fg=C["MUTED"], anchor="w",
                     font=(UIFONT, 11)).pack(fill="x", padx=14)

        # ── FREE: enter a key ──
        self.key_section = tk.Frame(inner, bg=C["SURF"])
        self.key_section.pack(fill="x")
        tk.Label(self.key_section, text="License key", bg=C["SURF"], fg=C["TEXT"], anchor="w",
                 font=(UIFONT, 12, "bold")).pack(fill="x", padx=14, pady=(14,2))
        row = tk.Frame(self.key_section, bg=C["SURF"]); row.pack(fill="x", padx=14)
        self.key_entry = tk.Entry(row, font=(MONO, 12), relief="flat",
                                  bg=C["BG"], fg=C["TEXT"], insertbackground=C["TEXT"])
        self.key_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0,8))
        self._btn(row, "Activate", lambda: self.activate_license(self.key_entry.get()), "gold").pack(side="right")
        buy = tk.Label(self.key_section, text="Get a license at zhmotions.com/pccleaner", bg=C["SURF"],
                       fg=C["MAROON2"], font=(UIFONT, 11, "underline"), cursor="hand2")
        buy.pack(anchor="w", padx=14, pady=14)
        buy.bind("<Button-1>", lambda e: os.startfile(SITE+"/pccleaner"))

        # ── PRO: manage / control (shown when activated) ──
        self.pro_section = tk.Frame(inner, bg=C["SURF"])
        self.lic_keylbl = tk.Label(self.pro_section, text="", bg=C["SURF"], fg=C["MUTED"],
                                   anchor="w", font=(MONO, 12))
        self.lic_keylbl.pack(fill="x", padx=14, pady=(14,8))
        mrow = tk.Frame(self.pro_section, bg=C["SURF"]); mrow.pack(fill="x", padx=14, pady=(0,12))
        self._btn(mrow, "Change key", self._change_key, "ghost").pack(side="left")
        self._btn(mrow, "Deactivate", self.deactivate_license, "ghost").pack(side="left", padx=8)

        self._refresh_license_ui()

    def _change_key(self):
        # show the entry again without losing Pro until a new key is activated
        self.pro_section.pack_forget(); self.key_section.pack(fill="x")
        self.key_entry.delete(0, "end"); self.key_entry.focus_set()

    def deactivate_license(self):
        if not messagebox.askyesno("Deactivate", "Remove the license from this PC? Pro features will lock."):
            return
        self.lic = {"key": "", "plan": "free", "valid": False, "checked": 0}
        try: LIC_FILE.unlink()
        except Exception: pass
        self._save_license()
        self._refresh_license_ui()
        self.q.put(("status", "License removed. Pro locked."))

    def _refresh_license_ui(self):
        if not hasattr(self, "lic_status"): return
        if self.is_pro():
            self.lic_status.config(text="● PRO — active ✓", fg=C["GREEN"])
            # hide the key entry, show the manage box (masked key + controls)
            self.key_section.pack_forget()
            self.pro_section.pack(fill="x")
            k = self.lic.get("key", "")
            masked = (k[:9] + "••••-" + k[-4:]) if len(k) > 13 else k
            self.lic_keylbl.config(text="Licensed key:  " + masked)
        else:
            self.lic_status.config(text="○ Free version", fg=C["MUTED"])
            self.pro_section.pack_forget()
            self.key_section.pack(fill="x")
        if "license" in self.nav_btns:
            self.nav_btns["license"].config(text="   ⭐   " + ("Pro ✓" if self.is_pro() else "Pro"))

    # ══ HELP & ABOUT ══
    def _build_help(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["help"] = v
        inner = self._scroller(v)
        def section(title, body):
            tk.Label(inner, text=title, bg=C["SURF"], fg=C["MAROON"], anchor="w",
                     font=(UIFONT, 13, "bold")).pack(fill="x", padx=14, pady=(12,2))
            tk.Label(inner, text=body, bg=C["SURF"], fg=C["TEXT"], anchor="w", justify="left",
                     font=(UIFONT, 11), wraplength=520).pack(fill="x", padx=14, pady=(0,4))

        tk.Label(inner, text="What is ZH PC Cleaner?", bg=C["SURF"], fg=C["TEXT"],
                 font=(UIFONT, 16, "bold")).pack(anchor="w", padx=14, pady=(14,2))
        tk.Label(inner, text="A safe, simple Windows cleaner. It frees disk space by removing junk that "
                 "Windows rebuilds automatically — and never touches system files.",
                 bg=C["SURF"], fg=C["MUTED"], anchor="w", justify="left",
                 font=(UIFONT, 11), wraplength=520).pack(fill="x", padx=14)

        section("🧹  Cleanup", "Deletes temp files, app caches and browser caches. These regenerate on their "
                "own — safe to remove. Tick what you want and press “Clean Selected”.")
        section("📦  Large Files", "Finds files over 100 MB in Downloads, Desktop, Documents, Videos & Pictures. "
                "Pick the ones you don't need — they go to the Recycle Bin (recoverable).")
        section("🗑️  Uninstaller", "Runs a program's uninstaller AND clears leftover AppData folders "
                "(caches, settings) that normally stay behind after uninstalling.")
        section("👯  Duplicates", "Finds identical files (same content). Keeps the first copy, lets you "
                "send the extras to the Recycle Bin to reclaim space.")
        section("🛠  Maintenance — what each tool does",
                "•  Flush DNS — clears the DNS cache. Fixes sites that won't load or point to an old server.\n"
                "•  Clear Update Cache — stops Windows Update, clears its download cache, restarts it (needs admin).\n"
                "•  Empty Recycle Bin — permanently frees the space used by deleted files.\n"
                "•  Disk Cleanup — opens the built-in Windows Disk Cleanup for a deeper clean.\n\n"
                "Some need admin (UAC) — run the app as administrator for those.")

        section("🔒  Is it safe?", "Yes. ZH PC Cleaner only touches a fixed list of safe user folders. "
                "Caches/temp are rebuilt by Windows; your own files go to the Recycle Bin so you can restore them. "
                "It never deletes documents, photos or system files.")
        section("💡  Run as administrator", "For the Update-cache and some system folders, right-click "
                "ZH PC Cleaner → Run as administrator so it can clear everything.")

        # Branding footer
        brand = tk.Frame(inner, bg=C["SURF"]); brand.pack(fill="x", padx=14, pady=18)
        if self.logo_img:
            tk.Label(brand, image=self.logo_img, bg=C["SURF"]).pack(side="left", padx=(0,10))
        col = tk.Frame(brand, bg=C["SURF"]); col.pack(side="left")
        tk.Label(col, text=f"ZH PC Cleaner  ·  v{APP_VERSION}", bg=C["SURF"], fg=C["MAROON"],
                 font=(UIFONT, 12, "bold")).pack(anchor="w")
        tk.Label(col, text="Made by ZH Motions", bg=C["SURF"], fg=C["MUTED"],
                 font=(UIFONT, 10)).pack(anchor="w")
        link = tk.Label(col, text="zhmotions.com", bg=C["SURF"], fg=C["MAROON2"],
                        font=(UIFONT, 10, "underline"), cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: os.startfile(SITE))
        self._btn(col, "↻  Check for Updates", lambda: self.check_updates(False), "gold").pack(anchor="w", pady=(8,0))

    def check_updates(self, silent=True):
        if not silent: self.q.put(("status", "Checking zhmotions.com for updates…"))
        def run():
            for name, url, kind in UPDATE_SOURCES:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    data = json.loads(urllib.request.urlopen(req, timeout=8, context=SSL_CTX).read().decode())
                    if kind == "zhm":
                        latest = str(data.get("version", "")).strip().lstrip("v")
                        dl     = data.get("download_url") or SITE
                        notes  = data.get("notes", "")
                    else:  # github releases/latest
                        latest = str(data.get("tag_name", "")).strip().lstrip("v")
                        dl     = data.get("html_url") or SITE
                        notes  = (data.get("body") or "")[:200]
                    if not latest:
                        continue
                    if self._is_newer(latest, APP_VERSION):
                        self.q.put(("update", (latest, dl, notes)))
                    elif not silent:
                        self.q.put(("status", f"✅ You're on the latest (v{APP_VERSION})."))
                    return  # first source that answered wins
                except Exception:
                    continue
            if not silent:
                self.q.put(("status", "⚠ Update check failed (no internet or site offline)."))
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _is_newer(a, b):
        def parts(v): return [int(x) for x in v.split(".") if x.isdigit()]
        return parts(a) > parts(b)

    def _show_update(self, latest, url, notes):
        if messagebox.askyesno("Update available",
            f"ZH PC Cleaner v{latest} is available (you have v{APP_VERSION}).\n\n"
            f"{notes}\n\nDownload from zhmotions.com now?"):
            os.startfile(url)


if __name__ == "__main__":
    try:
        Cleaner().mainloop()
    except Exception:
        import traceback, tempfile
        err = traceback.format_exc()
        try:
            with open(os.path.join(tempfile.gettempdir(), "zh-pc-cleaner-error.log"), "w", encoding="utf-8") as fh:
                fh.write(err)
        except Exception:
            pass
        try:
            from tkinter import messagebox as _mb
            _mb.showerror("ZH PC Cleaner — startup error", err[-1500:])
        except Exception:
            pass
        raise
