# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the BAM Windows EXE.

Build:  uv run pyinstaller bam.spec --noconfirm
Result: dist/bam/bam.exe  (onedir console bundle)

Design decisions:
- onedir: fast startup, fewer antivirus false positives than onefile.
- No data files bundled: config/ and data/ are resolved NEXT TO the
  executable at runtime (bam/config.py is frozen-aware). That keeps secrets,
  the production database and denylist policy external and user-editable.
- Nothing from data/, backups/ or tests/ can enter the bundle: only imported
  modules are analyzed, and none of those directories are importable content.
"""

a = Analysis(
    ["bam/cli.py"],
    pathex=["."],
    binaries=[],
    datas=[],           # deliberately empty - see module docstring
    hiddenimports=[],   # httpx/pyyaml trees are resolved from real imports
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "pytest", "setuptools", "tkinter", "unittest", "pydoc_data",
        "test", "tests", "xmlrpc", "distutils",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="bam",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,        # CLI tool
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="bam",
)
