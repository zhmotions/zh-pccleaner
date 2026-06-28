import sys
from cx_Freeze import setup, Executable

APP = "ZH PC Cleaner"
VERSION = "1.0.4"

build_exe_options = {
    "packages": ["tkinter", "ctypes", "ssl", "urllib", "hashlib", "json", "threading", "queue", "shutil"],
    "includes": ["certifi"],
    "include_files": [("assets", "assets")],
    "include_msvcr": True,
    "excludes": ["test", "unittest", "pydoc_data"],
}

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
