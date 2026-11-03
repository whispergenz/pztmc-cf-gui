# Build on Windows: .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm MinecraftTunnel.spec
from pathlib import Path

root = Path(SPECPATH)
cloudflared = sorted((root / "cloudflared").glob("cloudflared-*"))
a = Analysis(
    [str(root / 'cfgui-main.py')],
    pathex=[str(root)],
    binaries=[(str(path), 'cloudflared') for path in cloudflared if path.is_file()],
    datas=[], hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='MinecraftTunnel', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
)
