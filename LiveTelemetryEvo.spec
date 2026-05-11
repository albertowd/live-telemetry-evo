# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for LiveTelemetryEvo.

Built around the CLI flags ``build.py`` used to pass directly, plus a
post-Analysis filter that strips Qt6 binaries the runtime never touches.
PyInstaller's PySide6 hook collects QtQuick/QtQml/QtNetwork/QtPdf/etc.
unconditionally, regardless of ``--exclude-module``; the only reliable
way to drop them is to filter ``a.binaries`` after Analysis runs.

Invoked indirectly via ``build.py``, which converts ``resources/icon.png``
into ``resources/icon.ico`` first, then calls ``pyinstaller`` against
this file.
"""
from __future__ import annotations

import re
from pathlib import Path


# When PyInstaller runs us, ``__file__`` isn't defined on every Python
# version, but the spec is always invoked from the project root with
# ``pyinstaller LiveTelemetryEvo.spec``, so ``Path.cwd()`` is the source root.
ROOT = Path.cwd()
PROJECT = "LiveTelemetryEvo"
ENTRYPOINT = ROOT / "src" / "overlay" / "__main__.py"
RES_DIR = ROOT / "resources"
ICON_PNG = RES_DIR / "icon.png"
ICON_ICO = RES_DIR / "icon.ico"


def _read_version() -> str:
    """Mirror of ``build.py:_read_version`` — kept inline so the spec is
    self-sufficient when run directly via ``pyinstaller LiveTelemetryEvo.spec``."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not m:
        raise RuntimeError("could not parse 'version' from pyproject.toml")
    return m.group(1)


VERSION = _read_version()
NAME = f"{PROJECT}-{VERSION}"


# Modules whose DLLs PyInstaller would otherwise collect via the PySide6
# hook. Listing them in ``excludes`` keeps the Python wrappers (.pyd) out
# of the bundle even when the hook tries to add them — the binary filter
# below handles the actual Qt6*.dll files.
EXCLUDED_MODULES = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtConcurrent",
    "PySide6.QtDataVisualization", "PySide6.QtDBus", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtLocation", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNetwork", "PySide6.QtNetworkAuth",
    "PySide6.QtNfc", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning",
    "PySide6.QtPrintSupport", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtSvg",
    "PySide6.QtSvgWidgets", "PySide6.QtTest", "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools", "PySide6.QtVirtualKeyboard", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets", "PySide6.QtXml",
    # Heavy non-Qt deps the runtime never imports.
    "PIL", "numpy", "pyqtgraph", "tkinter",
]


# Substring matches against the bundle-relative dest path of each binary.
# Anything matching gets dropped from ``a.binaries``. Path comparisons use
# forward slashes — PyInstaller normalises both Windows and POSIX entries
# to forward slashes for the dest name.
UNWANTED_BINARIES = (
    # Software OpenGL fallback (~20 MB). Qt picks the system OpenGL/D3D
    # by default; this is only a last-resort fallback we don't need.
    "opengl32sw.dll",
    # QML / Quick — no QML in our code.
    "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6Quick.dll",
    "Qt6Quick3D.dll", "Qt6QuickControls2.dll", "Qt6QuickWidgets.dll",
    # OpenGL widgets — unused.
    "Qt6OpenGL.dll", "Qt6OpenGLWidgets.dll",
    # Networking + the OpenSSL pair it pulls in.
    "Qt6Network.dll", "libcrypto-", "libssl-",
    # PDF / SVG — both unused at runtime (fetch_icons.py uses QtSvg but
    # runs at build time outside the bundle).
    "Qt6Pdf.dll", "Qt6Svg.dll",
    # Image-format plugins. PNG support is built into Qt6Gui itself, so
    # we keep nothing else — the overlay only loads PNGs.
    "plugins/imageformats/qjpeg.dll",
    "plugins/imageformats/qwebp.dll",
    "plugins/imageformats/qtiff.dll",
    "plugins/imageformats/qgif.dll",
    "plugins/imageformats/qheif.dll",
    "plugins/imageformats/qico.dll",
    "plugins/imageformats/qpdf.dll",
    # Alternative Windows platform plugins — qwindows.dll is the one we
    # actually use. qdirect2d / qoffscreen / qminimal are unused.
    "plugins/platforms/qdirect2d.dll",
    "plugins/platforms/qoffscreen.dll",
    "plugins/platforms/qminimal.dll",
    # SQL drivers — none used.
    "plugins/sqldrivers/",
    # TLS / network plugins — needed only for QtNetwork.
    "plugins/tls/",
    "plugins/networkinformation/",
)


# Datas list — same as build.py used to pass via --add-data flags.
DATAS = [
    (str(RES_DIR / "img"), "resources/img"),
]
if ICON_PNG.exists():
    # Bundle the source PNG so the system-tray icon can load it at
    # runtime; the embedded .ico (icon=) only sets the EXE icon.
    DATAS.append((str(ICON_PNG), "resources"))


a = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=DATAS,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
)


# Drop the Qt6 binaries we don't actually use. PyInstaller stores
# binaries as 3-tuples ``(dest_name, source_path, kind)``; ``dest_name``
# uses forward slashes regardless of platform.
def _keep(entry) -> bool:
    dest = entry[0].replace("\\", "/")
    return not any(unwanted in dest for unwanted in UNWANTED_BINARIES)


_kept = [b for b in a.binaries if _keep(b)]
_dropped = [b for b in a.binaries if not _keep(b)]
print(f"[spec] keeping {len(_kept)} binaries; "
      f"dropped {len(_dropped)} ({sum(1 for _ in _dropped)} matches)")
for entry in _dropped:
    print(f"[spec]   drop  {entry[0]}")
a.binaries = _kept


pyz = PYZ(a.pure)


# Onefile mode: pass ``a.binaries`` and ``a.datas`` straight into EXE so
# the bootloader unpacks them at runtime. (No COLLECT step.)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX triggers AV false positives; skip it.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # equivalent of --windowed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_ICO) if ICON_ICO.exists() else None,
)
