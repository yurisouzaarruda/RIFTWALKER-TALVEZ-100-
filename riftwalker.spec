# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
datas = [(str(root / 'assets'), 'assets'), (str(root / 'CREDITS.txt'), '.'), (str(root / 'VERSION.txt'), '.'), (str(root / 'leaderboard_config.json'), '.')]

a = Analysis(
    ['main.py'],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RIFTWALKER',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(root / 'assets' / 'branding' / 'riftwalker.ico'),
)
