# Build on Windows: .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm MinecraftTunnel.spec
from pathlib import Path
import platform
import sys

root = Path(SPECPATH)


def cloudflared_candidates():
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        if machine in {"arm64", "aarch64"}:
            return ["cloudflared-windows-arm64.exe", "cloudflared-windows-386.exe"]
        return ["cloudflared-windows-amd64.exe", "cloudflared-windows-386.exe"]
    if sys.platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return ["cloudflared-darwin-arm64", "cloudflared-darwin-amd64"]
        return ["cloudflared-darwin-amd64", "cloudflared-darwin-arm64"]
    if machine in {"arm64", "aarch64"}:
        return ["cloudflared-linux-arm64", "cloudflared-linux-amd64"]
    if machine in {"armv7l", "armv6l", "arm"}:
        return ["cloudflared-linux-arm", "cloudflared-linux-arm64"]
    if machine in {"i386", "i686", "x86"}:
        return ["cloudflared-linux-386", "cloudflared-linux-amd64"]
    return ["cloudflared-linux-amd64", "cloudflared-linux-386"]


def find_cloudflared(binary_dir: Path) -> Path:
    for name in cloudflared_candidates():
        path = binary_dir / name
        if path.is_file():
            return path
    raise SystemExit(
        f"Error: No matching cloudflared binary found in {binary_dir} for {sys.platform} ({platform.machine()}). "
        f"Expected one of: {', '.join(cloudflared_candidates())}"
    )


cloudflared_bin = find_cloudflared(root / "cloudflared")

# Common unnecessary modules excluded to reduce bundle size
default_excludes = [
    'tkinter', 'unittest', 'email', 'html', 'http', 'xml',
    'pydoc', 'doctest', 'argparse', 'sqlite3', 'ssl',
    'multiprocessing', 'concurrent', 'asyncio', 'setuptools',
]

a = Analysis(
    [str(root / 'cfgui-main.py')],
    pathex=[str(root)],
    binaries=[(str(cloudflared_bin), 'cloudflared')],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=default_excludes,
    noarchive=False,
    optimize=2,  # Strips docstrings (-O2 optimization)
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MinecraftTunnel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,       # Removes symbol tables
    upx=True,         # Compresses binaries using UPX
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
)