import sys, re
from cx_Freeze import setup, Executable

APP = "ZH PC Cleaner"
# Read the single source of truth so the .msi version never drifts from the app.
VERSION = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                    open("zh_cleaner_win.py", encoding="utf-8").read()).group(1)

build_exe_options = {
    "packages": ["tkinter", "ctypes", "ssl", "urllib", "hashlib", "json", "threading", "queue", "shutil"],
    "includes": ["certifi"],
    "include_files": [("assets", "assets")],
    "include_msvcr": True,
    "excludes": ["test", "unittest", "pydoc_data"],
}

# AUTO-REMOVE OLD VERSION on install:
#   The stable `upgrade_code` (never change it) + a HIGHER `version` = a Windows MAJOR UPGRADE.
#   On install, MSI finds any prior version carrying this same upgrade_code and uninstalls it first,
#   then installs the new one — so the user never ends up with two copies. This ONLY fires when the
#   version number goes up, which is why APP_VERSION must be bumped every release.
#   NOTE: a copy that was installed by an OLD build WITHOUT this upgrade_code can't be auto-detected —
#   remove that one once via Settings → Apps (or the MS "Program Install and Uninstall" troubleshooter);
#   every install after that upgrades cleanly. Also quit the app before installing (a running .exe locks
#   its files and blocks the replace).
bdist_msi_options = {
    "upgrade_code": "{3F2A1B6C-9D4E-4A7B-8C1D-2E5F6A7B8C9D}",
    "add_to_path": False,
    "initial_target_dir": r"[ProgramFilesFolder]\ZH MOTIONS\ZH PC Cleaner",
    "all_users": True,
}

setup(
    name=APP,
    version=VERSION,
    description="ZH PC Cleaner — safe Windows cleaner by ZH Motions",
    author="ZH Motions",
    options={"build_exe": build_exe_options, "bdist_msi": bdist_msi_options},
    executables=[Executable(
        "zh_cleaner_win.py",
        base="Win32GUI",
        target_name="ZH PC Cleaner.exe",
        icon="AppIcon.ico",
        shortcut_name="ZH PC Cleaner",
        shortcut_dir="ProgramMenuFolder",
    )],
)
