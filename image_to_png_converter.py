"""
File Converter - by Tryppy (Multi-format) - Mac-styled UI
=======================================================

ImageGen - a PyQt6 desktop app that converts between common image formats (PNG, JPG,
WEBP, BMP, GIF, TIFF, ICO, and more), plus video-to-GIF and GIF-optimizing
tools.

UI style:
- Custom frameless title bar, macOS look and feel, but the minimize/close
  buttons sit on the RIGHT (Windows-style) instead of the native Apple
  left-side placement
- Overall macOS-ish light theme: rounded panels, soft grays, system font stack

Behavior:
- Toolbar "Open" button -> dropdown: "Choose a File" / "Choose Multiple Files"
- "Convert from" dropdown filters the file picker by source format (common
  formats + camera RAW formats). If you haven't touched this dropdown
  yourself, it auto-selects itself based on the format of whatever files
  you actually pick
- Only one format can be converted at a time. If Selected Files ends up
  with more than one file format in it, you'll get a warning, and each
  file gets its own "✕" button so you can remove the ones that don't
  belong before converting
- Up to 10 files queued at once
- Files no longer convert automatically on selection — they sit in
  "Selected Files" marked "ready" until the "Convert" button is pressed.
  Conversion then runs 1-2 at a time (a bounded thread pool) in the
  background, so it stays light on CPU/disk usage
- "Selected Files" (left): files waiting to be converted
- "Completed" (right): as soon as a file starts converting it moves here
  and shows an animated progress bar; once done the bar is replaced with
  green "Converted" text plus that file's own "Save" and "Save To" buttons
  (they save to disk immediately — conversion itself only happens in
  memory until then)
- Bottom "Download All" button (with a dropdown) saves every completed
  file at once: "Save to location found" or "Save to desired location"
- Title bar "Options" button -> "Delete original files after converting"
  (checkable). When on, saving a PNG (individually or via Download All)
  also deletes the original source file and shows a confirmation dialog
- Second tab, "Video to GIF": convert a single MP4 at a time with sliders
  for start time, length, FPS, and output width. "Generate Preview"
  actually renders the GIF (there's no reliable way to estimate GIF size
  without encoding it) and shows the real resulting file size before you
  commit to Save / Save To
- "WebP to GIF" tab: open a WebP (animated or still) and see it playing
  back live on the left as soon as it's loaded. Quality / Frames / Width
  sliders control palette size+dithering, how many of the original frames
  are kept (duration is stretched to preserve playback speed), and output
  width. Hit Convert to render the GIF and see it playing back live on
  the right, with a before/after size comparison, before Save / Save To
- "Web Images" tab (merged in from the old standalone Web Image Browser):
  a real embedded Chromium browser plus an Overlay Studio, with their own
  secondary nav bar inside the tab. Browse a page, collect every image it
  actually renders, preview them and save as PNG/GIF/WebP/AVIF, or send
  two of them into the Overlay Studio to composite one over the other
- "Image Creation" tab: local Stable Diffusion image generation (FLUX.1,
  SD3.5, SDXL...) from a description, optionally starting from reference
  images. Needs PyTorch + diffusers, which are not bundled
- "Background Remover" tab: click a background away, brush out the rest,
  and save the result as PNG/WebP/AVIF with real transparency. Uses an
  built-in AI subject detection (U^2-Net via onnxruntime)
- "WebP Flipbook" tab (merged in from the old standalone WebP Flipbook
  Converter): turns animated WebP *and* flipbook/sprite-sheet WebP into
  real animated GIFs, with automatic grid detection, a grid overlay on
  the preview, and optional PNG frame export

Requirements:
    pip install PyQt6 PyQt6-WebEngine Pillow pillow-heif rawpy moviepy
                imageio-ffmpeg requests numpy onnxruntime

    PyQt6-WebEngine / requests are only needed for the Web Images tab and
    numpy only for the flipbook's automatic grid detection - everything
    else keeps working without them.

Run:
    python image_to_png_converter.py
"""

import os
import sys
import base64
import subprocess
import enum
import bisect
import io
import shutil
import tempfile
import threading
import json
import platform
import traceback
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote, unquote_to_bytes, urlparse

# ---------------------------------------------------------------------------
# Extensions
#
# The main .exe stays small by leaving the heavy pieces out. Each optional
# tool ships as an "extension": a folder under extensions/<id>/ holding the
# packages that tool needs. Those folders go on the import path here, before
# anything optional is imported, so an installed extension simply makes its
# tool work and a missing one leaves the tool greyed out.
# ---------------------------------------------------------------------------

EXTENSIONS_DIRNAME = "extensions"


def app_folder():
    return os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))


def extensions_folder():
    return os.path.join(app_folder(), EXTENSIONS_DIRNAME)


def installed_extension_ids():
    folder = extensions_folder()
    if not os.path.isdir(folder):
        return set()
    return {name for name in os.listdir(folder)
            if os.path.isdir(os.path.join(folder, name))}


def activate_extensions():
    """Puts every installed extension's packages on the import path."""
    folder = extensions_folder()
    if not os.path.isdir(folder):
        return []
    activated = []
    for name in sorted(os.listdir(folder)):
        library = os.path.join(folder, name, "lib")
        target = library if os.path.isdir(library) else os.path.join(folder, name)
        if os.path.isdir(target):
            if target not in sys.path:
                sys.path.insert(0, target)
            activated.append(name)
    return activated


ACTIVE_EXTENSIONS = activate_extensions()

from PyQt6 import sip
from PyQt6.QtCore import (
    Qt, QEvent, QObject, QRunnable, QThreadPool, pyqtSignal, QSize, QUrl, QTimer,
    QPropertyAnimation, QEasingCurve, QRect, QSettings, QBuffer, QByteArray,
    QElapsedTimer, QIODevice, QPoint, QPointF, QRectF,
)
from PyQt6.QtGui import (
    QAction,
    QShortcut,
    QKeySequence,
    QMovie, QIcon, QPixmap, QImage, QDesktopServices, QBrush, QColor, QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QGroupBox,
    QListWidget,
    QListWidgetItem,
    QToolBar,
    QToolButton,
    QMenu,
    QComboBox,
    QLabel,
    QPushButton,
    QProgressBar,
    QSlider,
    QCheckBox,
    QTabWidget,
    QButtonGroup,
    QPlainTextEdit,
    QFileDialog,
    QMessageBox,
    QStatusBar,
    QSizeGrip,
    QInputDialog,
    QDialog,
    QScrollArea,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QSplitter,
    QSplitterHandle,
    QTabBar,
    QLayout,
    QBoxLayout,
    QStyle,
)

# Qt WebEngine powers the "Web Images" tab's embedded browser. It ships as a
# separate package (PyQt6-WebEngine) and must be imported before any
# QApplication exists, which is why it's up here rather than inline.
WEBENGINE_IMPORT_ERROR = None
try:
    # Qt 6 moved everything except the view itself into QtWebEngineCore.
    from PyQt6.QtWebEngineCore import (
        QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineScript,
    )
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001 - missing package, missing DLLs, etc.
    QWebEngineView = object
    QWebEnginePage = QWebEngineProfile = QWebEngineSettings = None
    QWebEngineScript = None
    WEBENGINE_AVAILABLE = False
    WEBENGINE_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

# requests gives the image downloader the same cookies/user-agent as the
# embedded browser. Without it there's a slower urllib fallback below.
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Qt 5 -> Qt 6 enum compatibility
#
# Qt 6 only exposes enum members through their enum type (Qt.AlignmentFlag.
# AlignCenter) where Qt 5 also allowed them straight off the class
# (Qt.AlignCenter). Rather than rewrite several hundred call sites - each one
# a chance to introduce a typo that only shows up when that code path runs -
# this re-publishes every enum member on its owning class, exactly as Qt 5
# did. Existing attributes are never overwritten.
# ---------------------------------------------------------------------------

def _publish_enum_members(*classes):
    for cls in classes:
        for attribute_name in dir(cls):
            candidate = getattr(cls, attribute_name, None)
            if not (isinstance(candidate, type) and issubclass(candidate, enum.Enum)):
                continue
            for name, member in candidate.__members__.items():
                if not hasattr(cls, name):
                    try:
                        setattr(cls, name, member)
                    except (AttributeError, TypeError):
                        pass


def _install_qt5_enum_compatibility():
    """Applies the shim to every Qt class this module imported."""
    _publish_enum_members(*[
        value for value in list(globals().values())
        if isinstance(value, type) and getattr(value, "__module__", "").startswith("PyQt6")
    ])


# numpy is used only by the flipbook tab's automatic grid detection.
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

_install_qt5_enum_compatibility()

try:
    from PIL import Image, ImageSequence
except ImportError:
    Image = None
    ImageSequence = None

# Pillow renamed these constants across versions (e.g. Image.LANCZOS moved
# under Image.Resampling in Pillow 10+). Resolve whichever is available.
if Image is not None:
    try:
        RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
    except AttributeError:
        RESAMPLE_LANCZOS = Image.LANCZOS
    # Frames here are always quantized while still in RGBA (to preserve
    # transparency), and Pillow only allows FASTOCTREE or libimagequant as
    # the method for RGBA source images - MEDIANCUT/others are RGB-only.
    try:
        QUANTIZE_METHOD = Image.Quantize.FASTOCTREE
    except AttributeError:
        QUANTIZE_METHOD = Image.FASTOCTREE
    # MEDIANCUT gives better colour on RGB source (used by the flipbook, which
    # converts to RGB itself before quantising).
    try:
        QUANTIZE_MEDIANCUT = Image.Quantize.MEDIANCUT
    except AttributeError:
        QUANTIZE_MEDIANCUT = Image.MEDIANCUT
    try:
        DITHER_NONE = Image.Dither.NONE
        DITHER_FLOYDSTEINBERG = Image.Dither.FLOYDSTEINBERG
    except AttributeError:
        DITHER_NONE = Image.NONE
        DITHER_FLOYDSTEINBERG = Image.FLOYDSTEINBERG
else:
    RESAMPLE_LANCZOS = None
    QUANTIZE_METHOD = None
    QUANTIZE_MEDIANCUT = None
    DITHER_NONE = None
    DITHER_FLOYDSTEINBERG = None

# Optional plugins - imported lazily/guarded so the app still runs without them
AVIF_AVAILABLE = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
    # pillow-heif 1.x dropped register_avif_opener (Pillow gained native AVIF
    # support), so this failing is normal on newer installs - the fallbacks
    # below pick it up instead of leaving AVIF switched off.
    try:
        pillow_heif.register_avif_opener()
        AVIF_AVAILABLE = True
    except Exception:  # noqa: BLE001
        AVIF_AVAILABLE = False
except ImportError:
    HEIF_AVAILABLE = False

if not AVIF_AVAILABLE:
    # pillow-avif-plugin registers itself with Pillow on import.
    try:
        import pillow_avif  # noqa: F401
        AVIF_AVAILABLE = True
    except ImportError:
        pass

if not AVIF_AVAILABLE and Image is not None:
    # Pillow 11.3+ can read and write AVIF on its own.
    try:
        from PIL import features
        AVIF_AVAILABLE = bool(features.check("avif"))
    except Exception:  # noqa: BLE001
        AVIF_AVAILABLE = False

if not AVIF_AVAILABLE and Image is not None:
    # Last resort: some builds register the plugin without advertising it
    # through features.check, so just ask Pillow whether it knows the format.
    AVIF_AVAILABLE = "AVIF" in getattr(Image, "MIME", {}) or \
        ".avif" in getattr(Image, "registered_extensions", lambda: {})()

AVIF_MISSING_MESSAGE = (
    "AVIF support isn't available in this Python. Install one of:\n"
    "    pip install pillow-avif-plugin\n"
    "    pip install --upgrade Pillow        (11.3 or newer has it built in)\n"
    "    pip install pillow-heif"
)

try:
    import rawpy
    RAWPY_AVAILABLE = True
except ImportError:
    RAWPY_AVAILABLE = False

# moviepy 2.x dropped the ".editor" submodule some 1.x installs still use,
# so try both import paths. The exact error is kept so the Video to GIF
# tab can show *why* it's unavailable, not just that it is.
MOVIEPY_IMPORT_ERROR = None
try:
    from moviepy.editor import VideoFileClip
except Exception as _exc:  # noqa: BLE001 - catch anything, not just ImportError
    try:
        from moviepy import VideoFileClip
    except Exception as _exc2:  # noqa: BLE001
        VideoFileClip = None
        MOVIEPY_IMPORT_ERROR = f"{type(_exc2).__name__}: {_exc2}"
MOVIEPY_AVAILABLE = VideoFileClip is not None


def resource_path(relative_path):
    """Resolves a path to a bundled resource (like the logo), whether
    running straight from source or from a PyInstaller-frozen exe. Frozen
    builds unpack --add-data files into sys._MEIPASS at runtime; running
    from source just looks next to this script."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


# Logo files, in the order they're preferred. iGlogo is the current branding;
# the older FClogo names are still accepted so an existing folder keeps
# working. Drop iGlogo.png and iGlogo.ico next to this script (or let
# build.bat bundle them) and they're picked up automatically.
LOGO_BASE_NAMES = ("iGlogo", "FClogo")


def _first_existing(*paths):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def logo_png_path():
    """The logo drawn inside the app's own title bar."""
    return _first_existing(*[resource_path(name + ".png") for name in LOGO_BASE_NAMES])


def logo_icon_path():
    """Windows wants a real multi-size .ico for the window and taskbar."""
    return _first_existing(*[resource_path(name + ".ico") for name in LOGO_BASE_NAMES])


LOGO_PATH = logo_png_path() or resource_path("iGlogo.png")
ICON_PATH = logo_icon_path() or resource_path("iGlogo.ico")


def app_icon():
    """The window/taskbar icon - .ico if there is one, PNG otherwise."""
    path = _first_existing(logo_icon_path(), logo_png_path())
    return QIcon(path) if path else QIcon()


# ---------------------------------------------------------------------------
# Branding and links - edit these freely.
#
# DISCORD_URL: paste your invite here and the "Visit our Discord" buttons
# start working. Leave it empty and they hide themselves.
# ---------------------------------------------------------------------------
DISCORD_URL = ""
WEBSITE_URL = "https://tko1.xyz"
MADE_BY = "Tryppy1"
MADE_WITH_TEXT = f"Made with \u2764 by {MADE_BY}"

# Which tool belongs to which installer component. A tool whose component
# wasn't installed is greyed out rather than missing, so it's obvious it
# exists and can be added.
COMPONENT_FILE = "installed_components.json"
COMPONENT_TABS = {
    "Video to GIF": "video_tools",
    "Video to Image": "video_tools",
    "Web Images": "web_images",
    "WebP Flipbook": "flipbook",
    "Background Remover": "background_remover",
    "Image Creation": "image_creation",
}
COMPONENT_NAMES = {
    "video_tools": "Video tools",
    "web_images": "Web Images",
    "flipbook": "WebP Flipbook",
    "background_remover": "Background Remover",
    "image_creation": "Image Creation",
}


def installed_components():
    """Which tools are available on this machine.

    A tool counts as available if the installer recorded it, or if its
    extension folder is present - installing an extension from inside the app
    shouldn't need the installer's manifest rewriting.

    Returns None when there's no manifest and no extensions folder at all,
    which is how it looks when running from source, and then everything is
    simply available.
    """
    path = os.path.join(app_folder(), COMPONENT_FILE)
    from_manifest = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                from_manifest = set(json.load(handle).get("installed", []))
        except Exception:  # noqa: BLE001
            from_manifest = None

    from_extensions = installed_extension_ids()
    if from_manifest is None and not from_extensions:
        return None
    return (from_manifest or set()) | from_extensions


APP_TITLE = "ImageGen"
APP_VERSION = "1.11.0"

# Update checking - looks at GitHub Releases for this repo. Create releases
# there with tags like "v1.1.0" and this will detect anything newer than
# APP_VERSION above.
GITHUB_OWNER = "TRYPWiRE"
GITHUB_REPO = "File-Converter"
GITHUB_LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)


def _parse_version(version_string):
    """Turns 'v1.2.3' / '1.2' / etc into a comparable tuple like (1, 2, 3)."""
    cleaned = version_string.strip().lstrip("vV")
    parts = []
    for chunk in cleaned.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


# ---------------------------------------------------------------------------
# Error reporting
#
# Every error the app shows goes through show_error(), which has a "Copy
# error" button. It copies a report with the app version, system and the
# full error, ready to paste into a message.
# ---------------------------------------------------------------------------

def error_report(title, message, details=""):
    lines = [
        f"{APP_TITLE} v{APP_VERSION} error report",
        f"Time: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"System: {platform.platform()}",
        f"Python: {platform.python_version()}"
        f"{' (built exe)' if getattr(sys, 'frozen', False) else ''}",
        f"Extensions: {', '.join(sorted(installed_extension_ids())) or 'none'}",
        "",
        f"Title: {title}",
        "",
        str(message).strip(),
    ]
    if details:
        lines += ["", "Details:", str(details).rstrip()]
    return "\n".join(lines)


class ErrorDialog(QDialog):
    """A warning box whose contents can be copied with one click."""

    def __init__(self, parent, title, message, details="", critical=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.report = error_report(title, message, details)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(14)
        icon = QLabel()
        standard = (QStyle.StandardPixmap.SP_MessageBoxCritical if critical
                    else QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon.setPixmap(self.style().standardIcon(standard).pixmap(40, 40))
        icon.setAlignment(Qt.AlignTop)
        top.addWidget(icon)
        text = QLabel(str(message))
        text.setWordWrap(True)
        text.setTextFormat(Qt.PlainText)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(text, 1)
        layout.addLayout(top)

        if details:
            box = QPlainTextEdit(str(details))
            box.setReadOnly(True)
            box.setMinimumHeight(140)
            layout.addWidget(box)

        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy error")
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setToolTip(
            "Copies the error and some details about your system, so you "
            "can paste it into a message.")
        self.copy_button.clicked.connect(self.copy_report)
        buttons.addWidget(self.copy_button)
        buttons.addStretch()
        ok_button = QPushButton("OK")
        ok_button.setObjectName("ConvertButton")
        ok_button.setCursor(Qt.PointingHandCursor)
        ok_button.setDefault(True)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        layout.addLayout(buttons)

    def copy_report(self):
        QApplication.clipboard().setText(self.report)
        self.copy_button.setText("Copied!")
        QTimer.singleShot(1500, lambda: self.copy_button.setText("Copy error"))


def show_error(parent, title, message, details="", critical=False):
    """Drop-in for QMessageBox.warning/critical, with a Copy error button."""
    ErrorDialog(parent, title, message, details, critical).exec()
    return QMessageBox.Ok


def show_critical(parent, title, message, details=""):
    return show_error(parent, title, message, details, critical=True)


class _ErrorRelay(QObject):
    """Carries errors from any thread to the GUI thread."""
    raised = pyqtSignal(str, str)


_error_relay = None


def install_error_hooks():
    """Shows anything that would otherwise crash silently in a copyable box."""
    global _error_relay
    _error_relay = _ErrorRelay()
    _error_relay.raised.connect(lambda message, details: show_error(
        QApplication.activeWindow(), "Something went wrong",
        f"{APP_TITLE} hit an unexpected error:\n\n{message}\n\n"
        "Copy the error and send it over so it can be fixed.",
        details=details, critical=True))

    def report(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(details)
        try:
            _error_relay.raised.emit(f"{exc_type.__name__}: {exc}", details)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = report
    threading.excepthook = lambda args: report(
        args.exc_type, args.exc_value, args.exc_traceback)


MAX_FILES = 10
MAX_CONCURRENT_CONVERSIONS = 2  # "1-2 at a time"

# QSettings persists small preferences (currently just the light/dark theme
# choice) between launches - QSettings stores this in the registry on
# Windows, or an ini file under ~/.config on Linux/Mac.
SETTINGS_ORG = "TryppyApps"
SETTINGS_APP = "FileConverter"

MIB = 1024 * 1024
# Quick target-size presets offered wherever a "compress to this size"
# feature is available, in addition to the Discord Emoji/Sticker presets.
TARGET_SIZE_PRESETS_MIB = [1, 8, 10, 20, 25, 50, 100, 500]

# Video to GIF tab defaults/limits
GIF_MAX_LENGTH_SECONDS = 20   # cap so nobody accidentally makes a 200MB gif
GIF_DEFAULT_FPS = 10
GIF_MIN_FPS = 5
GIF_MAX_FPS = 30
GIF_DEFAULT_WIDTH = 320
GIF_MIN_WIDTH = 100
GIF_MAX_WIDTH = 800

# Max box the animated preview scales to fit inside, so the whole frame is
# always visible regardless of the GIF's actual width/height
PREVIEW_BOX_MAX_WIDTH = 320
PREVIEW_BOX_MAX_HEIGHT = 240
# Size of an empty preview box before anything has been loaded into it
PREVIEW_PLACEHOLDER_WIDTH = 260
PREVIEW_PLACEHOLDER_HEIGHT = 170

# GIF Optimiser tab
GIF_OPT_MIN_COMPRESSION = 1
GIF_OPT_MAX_COMPRESSION = 100
GIF_OPT_DEFAULT_COMPRESSION = 40

IMAGE_OPT_MIN_COMPRESSION = 1
IMAGE_OPT_MAX_COMPRESSION = 100
IMAGE_OPT_DEFAULT_COMPRESSION = 40

# WebP to GIF tab
WEBP_QUALITY_MIN = 1
WEBP_QUALITY_MAX = 100
WEBP_QUALITY_DEFAULT = 80

# 100 = keep every frame; lower values thin the animation out (see
# _keep_every_for_frames_value below) while stretching each kept frame's
# duration to preserve the original playback speed.
WEBP_FRAMES_MIN = 10
WEBP_FRAMES_MAX = 100
WEBP_FRAMES_DEFAULT = 100

WEBP_WIDTH_MIN = 50
WEBP_WIDTH_MAX = 1000
WEBP_WIDTH_DEFAULT = 400

# Discord's actual limits: emoji must be under 256KB (128x128 is Discord's
# standard emoji canvas); stickers must be exactly 320x320 and under 512KB.
DISCORD_EMOJI_MAX_BYTES = 256 * 1024
DISCORD_EMOJI_SIZE_PX = 128
DISCORD_STICKER_MAX_BYTES = 512 * 1024
DISCORD_STICKER_SIZE_PX = 320

# Ordered so common formats show first, RAW formats after
FORMATS = {
    "All Supported Formats": None,  # filled in below
    "JPG / JPEG": ["*.jpg", "*.jpeg"],
    "PNG": ["*.png"],
    "BMP": ["*.bmp"],
    "GIF": ["*.gif"],
    "TIFF": ["*.tif", "*.tiff"],
    "ICO": ["*.ico"],
    "PSD": ["*.psd"],
    "WEBP": ["*.webp"],
    "HEIC / HEIF": ["*.heic", "*.heif"],
    "AVIF": ["*.avif"],
    "CR2 (Canon RAW)": ["*.cr2"],
    "CR3 (Canon RAW)": ["*.cr3"],
    "CRW (Canon RAW)": ["*.crw"],
    "NEF (Nikon RAW)": ["*.nef"],
    "ARW (Sony RAW)": ["*.arw"],
    "DNG (Adobe RAW)": ["*.dng"],
    "RAF (Fujifilm RAW)": ["*.raf"],
    "RW2 (Panasonic RAW)": ["*.rw2"],
    "ORF (Olympus RAW)": ["*.orf"],
    "PEF (Pentax RAW)": ["*.pef"],
    "DCR (Kodak RAW)": ["*.dcr"],
    "MRW (Minolta RAW)": ["*.mrw"],
    "MOS (Leaf RAW)": ["*.mos"],
    "3FR (Hasselblad RAW)": ["*.3fr"],
    "X3F (Sigma RAW)": ["*.x3f"],
    "ERF (Epson RAW)": ["*.erf"],
    "RAW (generic)": ["*.raw"],
}
_all_patterns = []
for _name, _patterns in FORMATS.items():
    if _patterns:
        _all_patterns.extend(_patterns)
FORMATS["All Supported Formats"] = _all_patterns

# Maps a file extension (e.g. ".webp") to the FORMATS dropdown entry name
# that covers it (e.g. "WEBP"), used to auto-select the dropdown based on
# whatever files the user actually picks.
EXTENSION_TO_FORMAT_NAME = {}
for _name, _patterns in FORMATS.items():
    if _name == "All Supported Formats" or not _patterns:
        continue
    for _pattern in _patterns:
        EXTENSION_TO_FORMAT_NAME[_pattern[1:].lower()] = _name

RAW_EXTENSIONS = {
    ".cr2", ".cr3", ".crw", ".nef", ".arw", ".dng", ".raf", ".rw2",
    ".orf", ".pef", ".dcr", ".mrw", ".mos", ".3fr", ".x3f", ".erf", ".raw",
}
HEIF_EXTENSIONS = {".heic", ".heif"}
AVIF_EXTENSIONS = {".avif"}

# "Convert to" options for the Images tab. Kept to formats Pillow can write
# reliably without extra plugins - PSD/RAW/etc. are read-only in Pillow so
# they're not offered as outputs.
OUTPUT_FORMATS = {
    "PNG": {"ext": ".png", "pillow_format": "PNG"},
    "JPG / JPEG": {"ext": ".jpg", "pillow_format": "JPEG"},
    "WEBP": {"ext": ".webp", "pillow_format": "WEBP"},
    "AVIF": {"ext": ".avif", "pillow_format": "AVIF"},
    "BMP": {"ext": ".bmp", "pillow_format": "BMP"},
    "GIF": {"ext": ".gif", "pillow_format": "GIF"},
    "TIFF": {"ext": ".tif", "pillow_format": "TIFF"},
    "ICO": {"ext": ".ico", "pillow_format": "ICO"},
}

# Writing AVIF needs the same plugin reading it does, so don't offer it as a
# "Convert to" option we can't actually deliver on.
if not AVIF_AVAILABLE:
    OUTPUT_FORMATS.pop("AVIF", None)

ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def _prepare_image_for_output(image, pillow_format):
    """Some formats can't hold an alpha channel (JPEG, BMP) and ICO needs
    an explicit list of embedded sizes - handle those quirks here so the
    caller can just call .save() with whatever this returns."""
    if pillow_format == "JPEG":
        # No alpha channel in JPEG - flatten transparency onto white.
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "RGBA":
            background.paste(image, mask=image.split()[3])
        else:
            background.paste(image.convert("RGB"))
        return background, {"quality": 95}

    if pillow_format == "BMP":
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "RGBA":
            background.paste(image, mask=image.split()[3])
        else:
            background.paste(image.convert("RGB"))
        return background, {}

    if pillow_format == "GIF":
        # Single still frame - just needs a palette, not a full alpha channel.
        return image.convert("P", palette=Image.ADAPTIVE), {}

    if pillow_format == "ICO":
        return image, {"sizes": ICO_SIZES}

    if pillow_format == "AVIF":
        # AVIF keeps alpha. 90 is visually near-lossless while still much
        # smaller than PNG, which is the usual reason for picking AVIF.
        return image, {"quality": 90}

    # PNG, TIFF, WEBP all handle RGBA directly.
    return image, {}


# ---------------------------------------------------------------------------
# macOS-ish stylesheet (applied app-wide)
# ---------------------------------------------------------------------------

MAC_STYLE = """
QWidget {
    background-color: #f5f5f7;
    font-family: -apple-system, "SF Pro Text", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #1d1d1f;
}

#TitleBar {
    background-color: #ececec;
    border-bottom: 1px solid #d6d6d6;
}

#TitleLabel {
    font-weight: 600;
    color: #3c3c3c;
}

QToolBar {
    background-color: #f5f5f7;
    border: none;
    padding: 6px 8px;
    spacing: 10px;
}

QGroupBox {
    background-color: #ffffff;
    border: 1px solid #d9d9dc;
    border-top: 2px solid #7a5cff;
    border-radius: 10px;
    margin-top: 16px;
    font-weight: 600;
    padding-top: 12px;
}

QGroupBox QLabel {
    background-color: transparent;
}

QGroupBox QCheckBox, QGroupBox QRadioButton {
    background-color: #ffffff;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #4a4a4d;
}

QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background-color: #e3e3e8;
}

QSlider::sub-page:horizontal {
    height: 6px;
    border-radius: 3px;
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #7a5cff, stop:1 #007aff
    );
}

QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background-color: #ffffff;
    border: 2px solid #7a5cff;
}

QSlider::handle:horizontal:hover {
    border-color: #6644ff;
    background-color: #f4f1ff;
}

QSlider::handle:horizontal:disabled {
    border-color: #c9c9cc;
    background-color: #f0f0f2;
}

QListWidget {
    background-color: #ffffff;
    border: 1px solid #e2e2e5;
    border-radius: 8px;
    padding: 4px;
}

QListWidget::item {
    border-radius: 5px;
}

QListWidget::item:selected {
    background-color: #d6e4ff;
}

QWidget#RowWidget {
    background-color: transparent;
}

QComboBox, QToolButton {
    background-color: #ffffff;
    border: 1px solid #d0d0d3;
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 18px;
}

QComboBox:hover, QToolButton:hover {
    background-color: #f0f0f2;
}

QPushButton {
    border-radius: 8px;
    padding: 8px 18px;
    min-height: 18px;
    font-weight: 600;
    border: 1px solid #c9c9cc;
    background-color: #ffffff;
}

QPushButton:hover {
    background-color: #eeeeee;
}

QPushButton#DownloadAllButton {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #7a5cff, stop:1 #007aff
    );
    color: #ffffff;
    border: none;
    padding: 8px 20px;
}

QPushButton#DownloadAllButton:hover {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #8f74ff, stop:1 #2b8bff
    );
}

QPushButton#ConvertButton {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #7a5cff, stop:1 #007aff
    );
    color: #ffffff;
    border: none;
    padding: 6px 18px;
}

QPushButton#ConvertButton:hover {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #8f74ff, stop:1 #2b8bff
    );
}

QPushButton#ConvertButton:disabled, QPushButton#DownloadAllButton:disabled {
    background: #d9d9dc;
    color: #ffffff;
}

QPushButton#RowSaveButton, QPushButton#RowSaveToButton {
    padding: 3px 10px;
    font-weight: 500;
    font-size: 12px;
    border-radius: 6px;
}

QPushButton#RowSaveButton:disabled, QPushButton#RowSaveToButton:disabled {
    color: #b5b5b8;
    background-color: #f5f5f7;
}

QPushButton#RemoveFileButton {
    border-radius: 10px;
    padding: 0px;
    font-weight: 700;
    font-size: 11px;
    border: none;
    background-color: #ececee;
    color: #6e6e73;
}

QPushButton#RemoveFileButton:hover {
    background-color: #ff3b30;
    color: #ffffff;
}

QProgressBar#NiceProgressBar {
    border: none;
    border-radius: 5px;
    background-color: #e5e5e7;
}

QProgressBar#NiceProgressBar::chunk {
    border-radius: 5px;
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #34c759, stop:1 #30d158
    );
}

QStatusBar {
    background-color: #f5f5f7;
    border-top: 1px solid #e2e2e5;
    color: #6e6e73;
}

QToolButton#ThemeToggle {
    border-radius: 12px;
    padding: 4px 12px;
    font-weight: 600;
}

QTabWidget::pane {
    border: none;
    background-color: #f5f5f7;
}

QWidget#PillTabBarContainer {
    background-color: #f5f5f7;
}

QPushButton#PillTabButton {
    background-color: transparent;
    color: #6e6e73;
    padding: 8px 13px;
    border: none;
    border-radius: 16px;
    font-weight: 600;
}

QPushButton#PillTabButton:hover {
    color: #1d1d1f;
}

QPushButton#PillTabButton:checked {
    background-color: #eef0ff;
    color: #1d1d1f;
}

QScrollArea#PillTabScroll, QScrollArea#PillTabScroll > QWidget > QWidget {
    background-color: #f5f5f7;
    border: none;
}

QScrollArea#PillTabScroll QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    margin: 0 14px;
}

QScrollArea#PillTabScroll QScrollBar::handle:horizontal {
    background: #d0d0d5;
    border-radius: 3px;
    min-width: 40px;
}

QScrollArea#PillTabScroll QScrollBar::add-line:horizontal,
QScrollArea#PillTabScroll QScrollBar::sub-line:horizontal {
    width: 0;
}

QScrollArea#PillTabScroll QScrollBar::add-page:horizontal,
QScrollArea#PillTabScroll QScrollBar::sub-page:horizontal {
    background: transparent;
}

QPushButton#PillTabButton[notInstalled="true"] {
    color: #b5b5b8;
    font-style: italic;
}

QWidget#TabIndicator {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #7a5cff, stop:1 #007aff
    );
    border-radius: 2px;
}

QPlainTextEdit#LogBox {
    background-color: #ffffff;
    border: 1px solid #d9d9dc;
    border-radius: 8px;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 11px;
    color: #4a4a4d;
    padding: 6px;
}
"""


# ---------------------------------------------------------------------------
# macOS-ish DARK stylesheet - same structure as MAC_STYLE, dark palette.
# Text/icon colors are kept light throughout so nothing goes invisible
# against the dark backgrounds.
# ---------------------------------------------------------------------------

DARK_STYLE = """
QWidget {
    background-color: #1e1e1e;
    font-family: -apple-system, "SF Pro Text", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #f2f2f2;
}

#TitleBar {
    background-color: #2b2b2d;
    border-bottom: 1px solid #3a3a3c;
}

#TitleLabel {
    font-weight: 600;
    color: #f2f2f2;
}

QToolBar {
    background-color: #1e1e1e;
    border: none;
    padding: 6px 8px;
    spacing: 10px;
}

QGroupBox {
    background-color: #2b2b2d;
    border: 1px solid #3a3a3c;
    border-top: 2px solid #9d7bff;
    border-radius: 10px;
    margin-top: 16px;
    font-weight: 600;
    padding-top: 12px;
    color: #f2f2f2;
}

QGroupBox QLabel {
    background-color: transparent;
}

QGroupBox QCheckBox, QGroupBox QRadioButton {
    background-color: #2b2b2d;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #d1d1d6;
}

QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background-color: #3a3a3c;
}

QSlider::sub-page:horizontal {
    height: 6px;
    border-radius: 3px;
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #9d7bff, stop:1 #0a84ff
    );
}

QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background-color: #2b2b2d;
    border: 2px solid #9d7bff;
}

QSlider::handle:horizontal:hover {
    border-color: #b295ff;
    background-color: #3a2f5c;
}

QSlider::handle:horizontal:disabled {
    border-color: #4a4a4c;
    background-color: #2b2b2d;
}

QListWidget {
    background-color: #2b2b2d;
    border: 1px solid #3a3a3c;
    border-radius: 8px;
    padding: 4px;
    color: #f2f2f2;
}

QListWidget::item {
    border-radius: 5px;
    color: #f2f2f2;
}

QListWidget::item:selected {
    background-color: #0a58ca;
    color: #ffffff;
}

QWidget#RowWidget {
    background-color: transparent;
}

QLabel {
    color: #f2f2f2;
}

QComboBox, QToolButton {
    background-color: #3a3a3c;
    border: 1px solid #4a4a4c;
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 18px;
    color: #f2f2f2;
}

QComboBox:hover, QToolButton:hover {
    background-color: #454547;
}

QComboBox QAbstractItemView {
    background-color: #2b2b2d;
    color: #f2f2f2;
    selection-background-color: #0a58ca;
    selection-color: #ffffff;
}

QMenu {
    background-color: #2b2b2d;
    color: #f2f2f2;
    border: 1px solid #3a3a3c;
}

QMenu::item:selected {
    background-color: #0a58ca;
    color: #ffffff;
}

QPushButton {
    border-radius: 8px;
    padding: 8px 18px;
    min-height: 18px;
    font-weight: 600;
    border: 1px solid #4a4a4c;
    background-color: #3a3a3c;
    color: #f2f2f2;
}

QPushButton:hover {
    background-color: #454547;
}

QPushButton#DownloadAllButton {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #9d7bff, stop:1 #0a84ff
    );
    color: #ffffff;
    border: none;
    padding: 8px 20px;
}

QPushButton#DownloadAllButton:hover {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #b295ff, stop:1 #3b9dff
    );
}

QPushButton#ConvertButton {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #9d7bff, stop:1 #0a84ff
    );
    color: #ffffff;
    border: none;
    padding: 6px 18px;
}

QPushButton#ConvertButton:hover {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #b295ff, stop:1 #3b9dff
    );
}

QPushButton#ConvertButton:disabled, QPushButton#DownloadAllButton:disabled {
    background: #3a3a3c;
    color: #8a8a8e;
}

QPushButton#RowSaveButton, QPushButton#RowSaveToButton {
    padding: 3px 10px;
    font-weight: 500;
    font-size: 12px;
    border-radius: 6px;
}

QPushButton#RowSaveButton:disabled, QPushButton#RowSaveToButton:disabled {
    color: #6e6e73;
    background-color: #2b2b2d;
    border: 1px solid #3a3a3c;
}

QPushButton#RemoveFileButton {
    border-radius: 10px;
    padding: 0px;
    font-weight: 700;
    font-size: 11px;
    border: none;
    background-color: #3a3a3c;
    color: #d1d1d6;
}

QPushButton#RemoveFileButton:hover {
    background-color: #ff453a;
    color: #ffffff;
}

QProgressBar#NiceProgressBar {
    border: none;
    border-radius: 5px;
    background-color: #3a3a3c;
}

QProgressBar#NiceProgressBar::chunk {
    border-radius: 5px;
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #30d158, stop:1 #34c759
    );
}

QStatusBar {
    background-color: #1e1e1e;
    border-top: 1px solid #3a3a3c;
    color: #a1a1a6;
}

QToolButton#ThemeToggle {
    border-radius: 12px;
    padding: 4px 12px;
    font-weight: 600;
}

QTabWidget::pane {
    border: none;
    background-color: #1e1e1e;
}

QWidget#PillTabBarContainer {
    background-color: #1e1e1e;
}

QPushButton#PillTabButton {
    background-color: transparent;
    color: #a1a1a6;
    padding: 8px 13px;
    border: none;
    border-radius: 16px;
    font-weight: 600;
}

QPushButton#PillTabButton:hover {
    color: #f2f2f2;
}

QPushButton#PillTabButton:checked {
    background-color: #2c2456;
    color: #f2f2f2;
}

QScrollArea#PillTabScroll, QScrollArea#PillTabScroll > QWidget > QWidget {
    background-color: #1e1e1e;
    border: none;
}

QScrollArea#PillTabScroll QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    margin: 0 14px;
}

QScrollArea#PillTabScroll QScrollBar::handle:horizontal {
    background: #48484a;
    border-radius: 3px;
    min-width: 40px;
}

QScrollArea#PillTabScroll QScrollBar::add-line:horizontal,
QScrollArea#PillTabScroll QScrollBar::sub-line:horizontal {
    width: 0;
}

QScrollArea#PillTabScroll QScrollBar::add-page:horizontal,
QScrollArea#PillTabScroll QScrollBar::sub-page:horizontal {
    background: transparent;
}

QPushButton#PillTabButton[notInstalled="true"] {
    color: #6e6e73;
    font-style: italic;
}

QWidget#TabIndicator {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #9d7bff, stop:1 #0a84ff
    );
    border-radius: 2px;
}

QPlainTextEdit#LogBox {
    background-color: #2b2b2d;
    border: 1px solid #3a3a3c;
    border-radius: 8px;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 11px;
    color: #d1d1d6;
    padding: 6px;
}
"""


# ---------------------------------------------------------------------------
# Filenames window - a small, non-modal popup listing the output filenames
# from a just-finished conversion, for easy copy/pasting elsewhere. Shown
# after saving from the Images tab or the WebP to GIF tab, only when
# "Show Filenames after conversion" is checked in the Options menu.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared named styles used by the Web Images and WebP Flipbook tabs (and a few
# app-wide niceties). Generated once per palette and appended to both themes,
# so switching Light/Dark restyles everything through the one app stylesheet -
# no per-widget sheets that can go stale.
# ---------------------------------------------------------------------------

THEME_PALETTES = {
    "light": {
        "bg": "#f5f5f7", "surface": "#ffffff", "surface2": "#fbfbfd",
        "border": "#d9d9dc", "input": "#ffffff", "input_border": "#d0d0d3",
        "text": "#1d1d1f", "muted": "#6e6e73", "accent": "#7a5cff",
        "chip_hover": "#f0f0f2", "scroll": "#c9c9ce",
        "disabled_bg": "#f2f2f4", "disabled_text": "#b5b5b8", "disabled_border": "#e3e3e6",
    },
    "dark": {
        "bg": "#1e1e1e", "surface": "#2b2b2d", "surface2": "#323236",
        "border": "#3a3a3c", "input": "#3a3a3c", "input_border": "#4a4a4c",
        "text": "#f2f2f2", "muted": "#a1a1a6", "accent": "#9d7bff",
        "chip_hover": "#454547", "scroll": "#55555a",
        "disabled_bg": "#2a2a2c", "disabled_text": "#6e6e73", "disabled_border": "#353537",
    },
}


def _spin_arrow_image(direction, color):
    """Renders a small anti-aliased triangle for the spin box buttons and
    returns its path for use in the stylesheet (Qt's stylesheet engine can't
    draw a clean triangle on its own). Returns None if it can't be made, in
    which case the buttons simply show no arrow."""
    if Image is None:
        return None
    try:
        from PIL import ImageDraw
        folder = os.path.join(tempfile.gettempdir(), "imagegen_ui")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"spin_{direction}_{color.lstrip('#')}.png")
        if not os.path.exists(path):
            scale = 4
            width, height = 9, 5
            big = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(big)
            w, h = width * scale, height * scale
            points = [(0, h), (w, h), (w / 2, 0)] if direction == "up" else \
                     [(0, 0), (w, 0), (w / 2, h)]
            draw.polygon(points, fill=color)
            big.resize((width, height), RESAMPLE_LANCZOS).save(path)
        return path.replace("\\", "/")
    except Exception:  # noqa: BLE001 - purely cosmetic
        return None


def _close_icon_image(color):
    """Small x for the browser tab close buttons."""
    if Image is None:
        return None
    try:
        from PIL import ImageDraw
        folder = os.path.join(tempfile.gettempdir(), "imagegen_ui")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"close_{color.lstrip('#')}.png")
        if not os.path.exists(path):
            scale, size, pad = 4, 10, 2
            big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(big)
            low, high = pad * scale, (size - pad) * scale
            draw.line((low, low, high, high), fill=color, width=scale)
            draw.line((low, high, high, low), fill=color, width=scale)
            big.resize((size, size), RESAMPLE_LANCZOS).save(path)
        return path.replace("\\", "/")
    except Exception:  # noqa: BLE001 - purely cosmetic
        return None


def _arrow_rule(selector, path, width=9, height=5):
    if not path:
        return f"{selector} {{ image: none; }}"
    return f"{selector} {{ image: url({path}); width: {width}px; height: {height}px; }}"


def _shared_style(p):
    return f"""
QGroupBox QSlider {{
    background-color: transparent;
}}

QPushButton:disabled {{
    background-color: {p['disabled_bg']};
    color: {p['disabled_text']};
    border: 1px solid {p['disabled_border']};
}}

QLineEdit, QAbstractSpinBox {{
    background-color: {p['input']};
    color: {p['text']};
    border: 1px solid {p['input_border']};
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 18px;
    selection-background-color: {p['accent']};
}}

QAbstractSpinBox {{
    padding-right: 22px;
}}

QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border;
    width: 20px;
    border: none;
    border-left: 1px solid {p['input_border']};
    background-color: transparent;
}}

QAbstractSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: 6px;
}}

QAbstractSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: 6px;
}}

QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
    background-color: {p['chip_hover']};
}}

{_arrow_rule("QAbstractSpinBox::up-arrow", _spin_arrow_image("up", p['muted']))}

{_arrow_rule("QAbstractSpinBox::down-arrow", _spin_arrow_image("down", p['muted']))}

{_arrow_rule("QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off", _spin_arrow_image("up", p['input_border']))}

{_arrow_rule("QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off", _spin_arrow_image("down", p['input_border']))}

QComboBox {{
    padding-right: 28px;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}}

{_arrow_rule("QComboBox::down-arrow", _spin_arrow_image("down", p['muted']))}

{_arrow_rule("QComboBox::down-arrow:disabled", _spin_arrow_image("down", p['input_border']))}

QLineEdit:focus, QAbstractSpinBox:focus {{
    border: 1px solid {p['accent']};
}}

QLineEdit:disabled, QAbstractSpinBox:disabled {{
    color: {p['muted']};
}}

QLineEdit#UrlBar {{
    border-radius: 10px;
    padding: 8px 14px;
}}

QLabel#MutedLabel {{
    color: {p['muted']};
    background-color: transparent;
}}

QLabel#HintLabel {{
    color: {p['muted']};
    font-size: 11px;
    background-color: transparent;
}}

QLabel#PreviewBox {{
    background-color: {p['bg']};
    border: 1px solid {p['border']};
    border-radius: 10px;
    color: {p['muted']};
}}

QPushButton#IconButton {{
    padding: 0px;
    min-height: 0px;
    font-size: 17px;
    font-weight: 700;
}}

QPushButton#SubNavButton {{
    background-color: transparent;
    color: {p['muted']};
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 8px 16px;
}}

QPushButton#SubNavButton:hover {{
    color: {p['text']};
}}

QPushButton#SubNavButton:checked {{
    color: {p['text']};
    border-bottom: 2px solid {p['accent']};
}}

QPushButton#ChipButton {{
    padding: 6px 6px;
    font-size: 12px;
}}

QPushButton#ChipButton:hover {{
    background-color: {p['chip_hover']};
}}

QPushButton#ChipButton:checked {{
    background-color: {p['accent']};
    border: 1px solid {p['accent']};
    color: #ffffff;
}}

QPushButton#ChipButton:disabled {{
    color: {p['muted']};
}}

QFrame#ImageCard {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 12px;
}}

QFrame#ImageCard QLabel {{
    background-color: transparent;
}}

QFrame#Slot {{
    background-color: {p['surface2']};
    border: 1px dashed {p['input_border']};
    border-radius: 10px;
}}

QFrame#Slot[dragOver="true"] {{
    border: 1px dashed {p['accent']};
}}

QFrame#Slot QLabel {{
    background-color: transparent;
}}

QFrame#Slot QLabel#PreviewBox {{
    background-color: {p['bg']};
}}

QTabBar#BrowserTabs {{
    background: transparent;
}}

QTabBar#BrowserTabs::tab {{
    background-color: {p['surface2']};
    color: {p['muted']};
    border: 1px solid {p['border']};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 10px;
    margin-right: 2px;
    max-width: 220px;
}}

QTabBar#BrowserTabs::tab:selected {{
    background-color: {p['surface']};
    color: {p['text']};
    border-bottom: 2px solid {p['accent']};
}}

QTabBar#BrowserTabs::tab:hover {{
    color: {p['text']};
}}

{_arrow_rule("QTabBar#BrowserTabs::close-button", _close_icon_image(p['muted']), 10, 10)}

{_arrow_rule("QTabBar#BrowserTabs::close-button:hover", _close_icon_image(p['accent']), 10, 10)}

QFrame#Divider {{
    background-color: {p['border']};
    border: none;
}}

QGraphicsView#OverlayPreview {{
    background-color: {p['bg']};
    border: 1px solid {p['border']};
    border-radius: 10px;
}}

QScrollArea#TabScroll, QScrollArea#PanelScroll {{
    background-color: transparent;
    border: none;
}}

QScrollArea#TabScroll > QWidget > QWidget,
QScrollArea#PanelScroll > QWidget > QWidget {{
    background-color: {p['bg']};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {p['scroll']};
    border-radius: 3px;
    min-height: 30px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: {p['scroll']};
    border-radius: 3px;
    min-width: 30px;
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0px;
    height: 0px;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
"""


MAC_STYLE += _shared_style(THEME_PALETTES["light"])
DARK_STYLE += _shared_style(THEME_PALETTES["dark"])


class FilenamesWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Converted Filenames")
        self.setMinimumSize(420, 360)
        self._filenames = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        caption = QLabel("Filenames of the files just converted:")
        layout.addWidget(caption)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        self.rows_layout.addStretch()  # rows get inserted above this

        self.scroll_area.setWidget(self.rows_container)
        layout.addWidget(self.scroll_area, stretch=1)

        hint = QLabel("Each line has its own Copy button, or use \"Copy All\" below.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6e6e73; font-size: 11px;")
        layout.addWidget(hint)

        buttons_row = QHBoxLayout()
        copy_all_button = QPushButton("Copy All")
        copy_all_button.setCursor(Qt.PointingHandCursor)
        copy_all_button.clicked.connect(self.on_copy_all)
        buttons_row.addWidget(copy_all_button)
        buttons_row.addStretch()

        close_button = QPushButton("Close")
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.clicked.connect(self.close)
        buttons_row.addWidget(close_button)

        layout.addLayout(buttons_row)

    def set_filenames(self, filenames):
        self._filenames = list(filenames)

        # Clear out any rows from a previous conversion (everything except
        # the trailing stretch at the end of rows_layout).
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for filename in self._filenames:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            field = QLineEdit(filename)
            field.setReadOnly(True)
            field.setCursorPosition(0)
            row_layout.addWidget(field, stretch=1)

            copy_button = QPushButton("Copy")
            copy_button.setCursor(Qt.PointingHandCursor)
            copy_button.setFixedWidth(64)
            copy_button.clicked.connect(
                lambda checked=False, text=filename: QApplication.clipboard().setText(text)
            )
            row_layout.addWidget(copy_button)

            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row_widget)

    def on_copy_all(self):
        QApplication.clipboard().setText("\n".join(self._filenames))


# ---------------------------------------------------------------------------
# Custom title bar - mac look, but min/close on the RIGHT
# ---------------------------------------------------------------------------

class TitleBar(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self._window = parent
        self.setObjectName("TitleBar")
        self.setFixedHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(8)

        logo_pixmap = QPixmap(LOGO_PATH)
        if not logo_pixmap.isNull():
            logo_label = QLabel()
            logo_label.setPixmap(
                logo_pixmap.scaled(
                    22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
            layout.addWidget(logo_label)

        title_label = QLabel(APP_TITLE)
        title_label.setObjectName("TitleLabel")
        layout.addWidget(title_label)
        layout.addStretch()

        # One Settings window instead of a dropdown of odds and ends.
        self.settings_button = QToolButton()
        self.settings_button.setText("\u2699  Settings")
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.clicked.connect(
            lambda checked=False: self._window.show_settings()
        )
        layout.addWidget(self.settings_button)

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("ThemeToggle")
        self.theme_button.setText("Light")
        self.theme_button.setPopupMode(QToolButton.InstantPopup)
        self.theme_button.setCursor(Qt.PointingHandCursor)

        theme_menu = QMenu(self.theme_button)
        light_action = QAction("Light", self)
        light_action.triggered.connect(lambda: self._window.set_theme("light"))
        theme_menu.addAction(light_action)

        dark_action = QAction("Dark", self)
        dark_action.triggered.connect(lambda: self._window.set_theme("dark"))
        theme_menu.addAction(dark_action)

        self.theme_button.setMenu(theme_menu)
        layout.addWidget(self.theme_button)

        self.minimize_button = self._make_circle_button("—", "#FEBC2E", "#ffd479")
        self.maximize_button = self._make_circle_button("▢", "#28C840", "#6fe088")
        self.close_button = self._make_circle_button("✕", "#FF5F57", "#ff8c85")
        self.maximize_button.setToolTip("Maximise")

        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self.minimize_button.clicked.connect(self._window.showMinimized)
        self.maximize_button.clicked.connect(self._window.toggle_maximised)
        self.close_button.clicked.connect(self._window.close)

        self._drag_pos = None

    def update_theme_label(self, theme_name):
        self.theme_button.setText("Dark" if theme_name == "dark" else "Light")

    def _make_circle_button(self, symbol, color, hover_color):
        btn = QPushButton(symbol)
        btn.setFixedSize(26, 26)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {color};
                border-radius: 13px;
                border: none;
                color: rgba(0, 0, 0, 0.55);
                font-weight: bold;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {hover_color};
            }}
        """)
        return btn

    def update_maximise_label(self, maximised):
        self.maximize_button.setText("❐" if maximised else "▢")
        self.maximize_button.setToolTip("Restore" if maximised else "Maximise")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._window.toggle_maximised()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() != Qt.LeftButton or self._drag_pos is None:
            return
        cursor = event.globalPosition().toPoint()
        if self._window.is_maximised():
            # Dragging a maximised window restores it and keeps it under the
            # cursor, the way a normal title bar behaves.
            ratio = 0.5
            if self.width():
                ratio = max(0.0, min(1.0, event.position().x() / self.width()))
            self._window.toggle_maximised()
            restored_width = self._window.width()
            self._drag_pos = QPoint(int(restored_width * ratio), self._drag_pos.y())
        self._window.move(cursor - self._drag_pos)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ---------------------------------------------------------------------------
# Per-row widget shown in the Selected Files list
# ---------------------------------------------------------------------------

class SelectedRowWidget(QWidget):
    """One row in the Selected Files list: filename/status text plus a
    small "X" button to remove that file before conversion starts."""

    def __init__(self, filename, tooltip_path, on_remove, parent=None):
        super().__init__(parent)
        self.setObjectName("RowWidget")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self.text_label = QLabel(f"{filename} — ready")
        self.text_label.setToolTip(tooltip_path)
        layout.addWidget(self.text_label, stretch=1)

        self.remove_button = QPushButton("✕")
        self.remove_button.setObjectName("RemoveFileButton")
        self.remove_button.setFixedSize(20, 20)
        self.remove_button.setCursor(Qt.PointingHandCursor)
        self.remove_button.setToolTip("Remove this file")
        self.remove_button.clicked.connect(on_remove)
        layout.addWidget(self.remove_button)

    def set_text(self, text):
        self.text_label.setText(text)

    def set_removable(self, removable):
        self.remove_button.setEnabled(removable)
        self.remove_button.setVisible(removable)


# ---------------------------------------------------------------------------
# Per-row widget shown in the Completed list
# ---------------------------------------------------------------------------

class CompletedRowWidget(QWidget):
    """One row in the Completed list: filename, a progress bar while
    converting, then green "Converted" text plus Save / Save To buttons
    once the in-memory conversion is done."""

    def __init__(self, filename, tooltip_path, on_save, on_save_to, parent=None):
        super().__init__(parent)
        self.setObjectName("RowWidget")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        self.name_label = QLabel(filename)
        self.name_label.setToolTip(tooltip_path)
        self.name_label.setMinimumWidth(150)
        layout.addWidget(self.name_label, stretch=2)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("NiceProgressBar")
        self.progress_bar.setRange(0, 0)  # animated "busy" style
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        layout.addWidget(self.progress_bar, stretch=3)

        self.status_label = QLabel("Converted")
        self.status_label.hide()
        layout.addWidget(self.status_label, stretch=3)

        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("RowSaveButton")
        self.save_button.setToolTip("Saves to same location file was found")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(on_save)
        layout.addWidget(self.save_button)

        self.save_to_button = QPushButton("Save To")
        self.save_to_button.setObjectName("RowSaveToButton")
        self.save_to_button.setToolTip("Choose a location and file name to save to")
        self.save_to_button.setCursor(Qt.PointingHandCursor)
        self.save_to_button.setEnabled(False)
        self.save_to_button.clicked.connect(on_save_to)
        layout.addWidget(self.save_to_button)

    def mark_converted(self):
        self.progress_bar.hide()
        self.status_label.setText("Converted")
        self.status_label.setStyleSheet("color: #1fa851; font-weight: 700;")
        self.status_label.show()
        self.save_button.setEnabled(True)
        self.save_to_button.setEnabled(True)

    def mark_failed(self, message):
        self.progress_bar.hide()
        self.status_label.setText(f"Failed: {message}")
        self.status_label.setStyleSheet("color: #d93025; font-weight: 700;")
        self.status_label.show()

    def flash_saved(self):
        """Brief visual acknowledgement after a save completes."""
        original = self.status_label.text()
        self.status_label.setText("Saved ✓")
        self.status_label.setStyleSheet("color: #007aff; font-weight: 700;")

        def _restore():
            self.status_label.setText(original)
            self.status_label.setStyleSheet("color: #1fa851; font-weight: 700;")

        QTimer.singleShot(1200, _restore)


# ---------------------------------------------------------------------------
# Conversion worker (runs in a background thread pool) - decodes only,
# does not write to disk. Disk writes happen later via Save / Save To /
# Download All, using the in-memory image.
# ---------------------------------------------------------------------------

class WorkerSignals(QObject):
    started = pyqtSignal(str)
    finished = pyqtSignal(str, object)  # source_path, PIL Image
    failed = pyqtSignal(str, str)       # source_path, error_message


class ConversionWorker(QRunnable):
    def __init__(self, source_path):
        super().__init__()
        self.source_path = source_path
        self.signals = WorkerSignals()

    def run(self):
        self.signals.started.emit(self.source_path)
        ext = os.path.splitext(self.source_path)[1].lower()
        try:
            if ext in RAW_EXTENSIONS:
                if not RAWPY_AVAILABLE:
                    raise RuntimeError(
                        "RAW support needs 'rawpy' - run: pip install rawpy"
                    )
                with rawpy.imread(self.source_path) as raw:
                    rgb_array = raw.postprocess()
                img = Image.fromarray(rgb_array)
            else:
                if ext in HEIF_EXTENSIONS and not HEIF_AVAILABLE:
                    raise RuntimeError(
                        "HEIC/HEIF support needs 'pillow-heif' - "
                        "run: pip install pillow-heif"
                    )
                if ext in AVIF_EXTENSIONS and not AVIF_AVAILABLE:
                    raise RuntimeError(AVIF_MISSING_MESSAGE)
                img = Image.open(self.source_path)

            img = img.convert("RGBA")
            img.load()  # force full decode now, while still on this thread
            self.signals.finished.emit(self.source_path, img)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(self.source_path, str(exc))


# ---------------------------------------------------------------------------
# Video -> GIF worker (runs in the background thread pool)
# ---------------------------------------------------------------------------

class GifWorkerSignals(QObject):
    started = pyqtSignal()
    finished = pyqtSignal(str, int)  # temp_gif_path, size_in_bytes
    failed = pyqtSignal(str)


class GifConversionWorker(QRunnable):
    def __init__(self, source_path, start, length, fps, width, output_path):
        super().__init__()
        self.source_path = source_path
        self.start = start
        self.length = length
        self.fps = fps
        self.width = width
        self.output_path = output_path
        self.signals = GifWorkerSignals()

    def run(self):
        self.signals.started.emit()
        clip = None
        try:
            clip = VideoFileClip(self.source_path)
            end = min(self.start + self.length, clip.duration)

            # moviepy 2.x renamed subclip -> subclipped and resize -> resized.
            # Support whichever API is installed.
            if hasattr(clip, "subclipped"):
                sub = clip.subclipped(self.start, end)
            else:
                sub = clip.subclip(self.start, end)

            if self.width:
                if hasattr(sub, "resized"):
                    sub = sub.resized(width=self.width)
                else:
                    sub = sub.resize(width=self.width)

            # Build the GIF with Pillow instead of moviepy's own write_gif().
            # moviepy's internal GIF writer depends on how ffmpeg/ImageMagick
            # is set up and can fail silently (e.g. "'NoneType' object has no
            # attribute 'write'") depending on version/environment. moviepy is
            # still used here to decode frames (via ffmpeg), which is the part
            # that's actually reliable - Pillow then handles the GIF encoding.
            frames = [
                Image.fromarray(frame)
                for frame in sub.iter_frames(fps=self.fps, dtype="uint8")
            ]
            if not frames:
                raise RuntimeError("No frames could be read from this clip.")

            frame_duration_ms = max(int(1000 / self.fps), 1)
            frames[0].save(
                self.output_path,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=frame_duration_ms,
                loop=0,
            )

            size = os.path.getsize(self.output_path)
            self.signals.finished.emit(self.output_path, size)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))
        finally:
            if clip is not None:
                clip.close()


def _format_file_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


# ---------------------------------------------------------------------------
# Output size / aspect ratio
#
# Shared by the Images converter and both optimisers so "make this a square
# Instagram post" works the same way everywhere. The presets are the common
# social sizes; anything else can be typed in as a custom size.
# ---------------------------------------------------------------------------

RESIZE_PRESET_ORIGINAL = "Original size"
RESIZE_PRESET_CUSTOM = "Custom…"

RESIZE_PRESETS = [
    (RESIZE_PRESET_ORIGINAL, None),
    ("Square 1:1 — 1080 × 1080", (1080, 1080)),
    ("Portrait 4:5 — 1080 × 1350", (1080, 1350)),
    ("Story / Reel 9:16 — 1080 × 1920", (1080, 1920)),
    ("Landscape 16:9 — 1920 × 1080", (1920, 1080)),
    ("Landscape 16:9 — 1280 × 720", (1280, 720)),
    ("Link preview 1.91:1 — 1200 × 630", (1200, 630)),
    ("Banner 3:1 — 1500 × 500", (1500, 500)),
    ("Icon 1:1 — 512 × 512", (512, 512)),
    (RESIZE_PRESET_CUSTOM, "custom"),
]

RESIZE_MODES = [
    ("Fit — whole image, padded", "fit"),
    ("Fill — crop to fill", "fill"),
    ("Stretch — ignore ratio", "stretch"),
]


def resize_to_spec(image, spec):
    """Returns `image` at exactly spec["size"].

    fit     keeps the whole image and pads the leftover space (transparent,
            which JPEG output later flattens to white)
    fill    scales until the frame is covered, then centre-crops
    stretch scales each axis independently
    """
    if not spec:
        return image
    target_width, target_height = spec["size"]
    mode = spec.get("mode", "fit")
    source = image if image.mode == "RGBA" else image.convert("RGBA")
    width, height = source.size
    if (width, height) == (target_width, target_height):
        return source

    if not spec.get("upscale", True):
        # Never blow a small image up - just pad or crop it to the frame.
        if width <= target_width and height <= target_height and mode != "stretch":
            canvas = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
            canvas.paste(source, ((target_width - width) // 2,
                                  (target_height - height) // 2))
            return canvas

    if mode == "stretch":
        return source.resize((target_width, target_height), RESAMPLE_LANCZOS)

    if mode == "fill":
        ratio = max(target_width / width, target_height / height)
        scaled = source.resize((max(1, round(width * ratio)),
                                max(1, round(height * ratio))), RESAMPLE_LANCZOS)
        left = (scaled.width - target_width) // 2
        top = (scaled.height - target_height) // 2
        return scaled.crop((left, top, left + target_width, top + target_height))

    ratio = min(target_width / width, target_height / height)
    scaled = source.resize((max(1, round(width * ratio)),
                            max(1, round(height * ratio))), RESAMPLE_LANCZOS)
    canvas = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    canvas.paste(scaled, ((target_width - scaled.width) // 2,
                          (target_height - scaled.height) // 2))
    return canvas


def spec_adds_transparency(spec):
    """Fit padding creates see-through pixels, so the output has to keep
    alpha even when the source was fully opaque."""
    return bool(spec) and spec.get("mode", "fit") == "fit"


class ResizeOptionsWidget(QWidget):
    """One compact row of output-size controls, reused across the tabs."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        label = QLabel("Output size")
        row.addWidget(label)

        self.preset_combo = QComboBox()
        for name, _value in RESIZE_PRESETS:
            self.preset_combo.addItem(name)
        self.preset_combo.setToolTip(
            "Resize the output to a set shape - handy for social posts.\n"
            "Choose Custom… to type your own size."
        )
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        row.addWidget(self.preset_combo, 1)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 20000)
        self.width_spin.setValue(1080)
        self.width_spin.setSuffix(" px")
        self.width_spin.setMaximumWidth(110)
        row.addWidget(self.width_spin)
        self.times_label = QLabel("×")
        row.addWidget(self.times_label)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 20000)
        self.height_spin.setValue(1080)
        self.height_spin.setSuffix(" px")
        self.height_spin.setMaximumWidth(110)
        row.addWidget(self.height_spin)

        self.mode_combo = QComboBox()
        for name, _value in RESIZE_MODES:
            self.mode_combo.addItem(name)
        self.mode_combo.setToolTip(
            "Fit keeps the whole image and pads the gaps (see-through, or "
            "white where the format can't do transparency).\n"
            "Fill crops the overflow so the frame is covered.\n"
            "Stretch squashes the image to shape."
        )
        row.addWidget(self.mode_combo, 1)

        self.upscale_check = QCheckBox("Allow upscaling")
        self.upscale_check.setChecked(True)
        self.upscale_check.setToolTip(
            "Off: a picture smaller than the target is centred at its own "
            "size rather than being blown up and going soft."
        )
        row.addWidget(self.upscale_check)

        for widget in (self.width_spin, self.height_spin):
            widget.valueChanged.connect(lambda _value: self.changed.emit())
        self.mode_combo.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self.upscale_check.toggled.connect(lambda _checked: self.changed.emit())
        self._preset_changed(0)

    def _preset_changed(self, index):
        name, value = RESIZE_PRESETS[index]
        custom = value == "custom"
        active = value is not None
        if isinstance(value, tuple):
            self.width_spin.blockSignals(True)
            self.height_spin.blockSignals(True)
            self.width_spin.setValue(value[0])
            self.height_spin.setValue(value[1])
            self.width_spin.blockSignals(False)
            self.height_spin.blockSignals(False)
        for widget in (self.width_spin, self.height_spin, self.times_label):
            widget.setEnabled(custom)
        for widget in (self.mode_combo, self.upscale_check):
            widget.setEnabled(active)
        self.changed.emit()

    def spec(self):
        """The current setting, or None for 'leave the size alone'."""
        _name, value = RESIZE_PRESETS[self.preset_combo.currentIndex()]
        if value is None:
            return None
        if value == "custom":
            size = (self.width_spin.value(), self.height_spin.value())
        else:
            size = value
        return {
            "size": size,
            "mode": RESIZE_MODES[self.mode_combo.currentIndex()][1],
            "upscale": self.upscale_check.isChecked(),
        }


def _build_optimized_gif(source_path, compression_level, output_path,
                         target_square_size=None, resize_spec=None):
    """Re-encodes a GIF at a given compression level (1-100, higher = more
    compression/smaller/lower quality). If target_square_size is given,
    each frame is scaled to fit within that square and centered on a
    transparent canvas of exactly that size (used by the Discord presets).

    resize_spec (from ResizeOptionsWidget) instead pins the output to an
    exact size: every frame comes out at that size and the compression level
    then affects quality rather than dimensions. The Discord presets carry
    their own size, so they win if both are set."""
    frames = []
    durations = []
    with Image.open(source_path) as im:
        source_has_transparency = ("transparency" in im.info) or (im.mode in ("RGBA", "LA", "PA"))
        for frame in ImageSequence.Iterator(im):
            frames.append(frame.convert("RGBA").copy())
            durations.append(frame.info.get("duration", 100))

    if not frames:
        raise RuntimeError("No frames could be read from this GIF.")

    if target_square_size is not None:
        resize_spec = None  # the preset's own size takes precedence
    if resize_spec:
        frames = [resize_to_spec(frame, resize_spec) for frame in frames]

    # Padding onto a canvas always creates new transparent pixels, even if
    # the source GIF itself had none.
    needs_transparency = (source_has_transparency or target_square_size is not None
                          or spec_adds_transparency(resize_spec))

    # Higher compression -> fewer palette colors, smaller scale, fewer frames
    colors = max(8, int(256 - (compression_level / 100) * 248))
    scale = max(1.0 - (compression_level / 100) * 0.7, 0.15)
    if resize_spec:
        # The user asked for an exact output size, so compression works
        # through the palette and frame count rather than the dimensions.
        scale = 1.0
    keep_every = 1
    if compression_level > 60:
        keep_every = 2
    if compression_level > 80:
        keep_every = 3
    if compression_level > 95:
        keep_every = 4

    frame_outs = []
    new_durations = []
    for i, (frame, duration) in enumerate(zip(frames, durations)):
        if i % keep_every != 0:
            continue

        w, h = frame.size
        if target_square_size:
            # The output canvas is always exactly target_square_size (so
            # e.g. a Discord sticker stays a valid 320x320 file), but the
            # actual content inside it shrinks as compression increases -
            # otherwise the compression slider would have no effect on
            # resolution at all for these presets.
            content_box = max(int(target_square_size * scale), 16)
            ratio = min(content_box / w, content_box / h)
            new_w, new_h = max(1, int(w * ratio)), max(1, int(h * ratio))
            resized = frame.resize((new_w, new_h), RESAMPLE_LANCZOS)
            canvas = Image.new("RGBA", (target_square_size, target_square_size), (0, 0, 0, 0))
            canvas.paste(resized, ((target_square_size - new_w) // 2, (target_square_size - new_h) // 2), resized)
            frame_out = canvas
        else:
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
            frame_out = frame.resize((new_w, new_h), RESAMPLE_LANCZOS)

        frame_outs.append(frame_out)
        new_durations.append(duration * keep_every)

    if not frame_outs:
        raise RuntimeError("No frames left after optimizing.")

    extra_save_kwargs = {}

    if needs_transparency:
        # GIF only supports a single transparent palette index (not real
        # alpha) - and quantizing each frame to its OWN independent palette
        # means "transparent" can land on a different index in every frame.
        # To keep it consistent, transparent pixels are first replaced with
        # a distinct placeholder color, one shared palette is built from
        # the first frame, and every other frame reuses that exact palette -
        # so the placeholder (and therefore "transparent") always ends up
        # at the same index throughout the whole animation.
        key_color = (255, 0, 255)
        keyed_frames = []
        for frame_out in frame_outs:
            keyed = Image.new("RGB", frame_out.size, key_color)
            keyed.paste(frame_out, mask=frame_out.split()[3])
            keyed_frames.append(keyed)

        base_quantized = keyed_frames[0].quantize(colors=colors, method=QUANTIZE_METHOD)
        processed = [base_quantized] + [
            kf.quantize(palette=base_quantized) for kf in keyed_frames[1:]
        ]

        transparency_index = _find_palette_index(base_quantized, key_color)
        if transparency_index is not None:
            extra_save_kwargs["transparency"] = transparency_index
    else:
        processed = [
            frame_out.quantize(colors=colors, method=QUANTIZE_METHOD)
            for frame_out in frame_outs
        ]

    processed[0].save(
        output_path, format="GIF", save_all=True,
        append_images=processed[1:], duration=new_durations,
        loop=0, optimize=True, disposal=2,
        **extra_save_kwargs,
    )


def _find_palette_index(quantized_image, target_rgb):
    """Finds which palette index a given RGB color landed on after
    quantizing (used to locate the transparency key color's index)."""
    palette = quantized_image.getpalette()
    if not palette:
        return None

    best_index, best_distance = None, None
    for index in range(len(palette) // 3):
        r, g, b = palette[index * 3: index * 3 + 3]
        if (r, g, b) == target_rgb:
            return index
        distance = (r - target_rgb[0]) ** 2 + (g - target_rgb[1]) ** 2 + (b - target_rgb[2]) ** 2
        if best_distance is None or distance < best_distance:
            best_distance, best_index = distance, index
    return best_index


class GifOptimizeSignals(QObject):
    finished = pyqtSignal(str, int)  # output_path, size_bytes
    failed = pyqtSignal(str)


class GifOptimizeWorker(QRunnable):
    """Runs the optimizer once, at whatever compression level was chosen."""

    def __init__(self, source_path, output_path, compression_level,
                 target_square_size=None, resize_spec=None):
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.compression_level = compression_level
        self.target_square_size = target_square_size
        self.resize_spec = resize_spec
        self.signals = GifOptimizeSignals()

    def run(self):
        try:
            _build_optimized_gif(
                self.source_path, self.compression_level, self.output_path,
                target_square_size=self.target_square_size,
                resize_spec=self.resize_spec,
            )
            size = os.path.getsize(self.output_path)
            self.signals.finished.emit(self.output_path, size)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


class GifPresetSignals(QObject):
    finished = pyqtSignal(str, int, int, bool)  # output_path, size_bytes, level_used, met_target
    failed = pyqtSignal(str)


class GifPresetWorker(QRunnable):
    """Searches increasing compression levels until the result fits under
    target_bytes (and, for stickers, an exact square canvas), then stops -
    so the result is only as compressed as it needs to be."""

    def __init__(self, source_path, output_path, target_bytes, target_square_size,
                 resize_spec=None):
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.target_bytes = target_bytes
        self.target_square_size = target_square_size
        self.resize_spec = resize_spec
        self.signals = GifPresetSignals()

    def run(self):
        try:
            chosen_level = GIF_OPT_MAX_COMPRESSION
            met_target = False
            for level in range(10, GIF_OPT_MAX_COMPRESSION + 1, 10):
                _build_optimized_gif(
                    self.source_path, level, self.output_path,
                    target_square_size=self.target_square_size,
                    resize_spec=self.resize_spec,
                )
                size = os.path.getsize(self.output_path)
                chosen_level = level
                if size <= self.target_bytes:
                    met_target = True
                    break

            size = os.path.getsize(self.output_path)
            self.signals.finished.emit(self.output_path, size, chosen_level, met_target)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Video to GIF tab
# ---------------------------------------------------------------------------

class VideoToGifTab(QWidget):
    """Handles one MP4 at a time: pick a video, dial in trim/fps/width with
    sliders, hit Generate Preview to actually render the GIF and see its
    real file size, then Save / Save To to write it to disk."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.video_path = None
        self.video_duration = 0.0
        self.temp_gif_path = None

        self._build_ui()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        if not MOVIEPY_AVAILABLE:
            message = (
                "Video to GIF needs the 'moviepy' package (and ffmpeg via "
                "'imageio-ffmpeg').\nRun: pip install moviepy imageio-ffmpeg"
            )
            if MOVIEPY_IMPORT_ERROR:
                message += f"\n\nDetails: {MOVIEPY_IMPORT_ERROR}"
            warning = QLabel(message)
            warning.setWordWrap(True)
            warning.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(warning)
            layout.addStretch()
            return

        open_button = QPushButton("Open Video…")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.clicked.connect(self.choose_video)
        layout.addWidget(open_button)

        self.file_label = QLabel("No video selected")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        sliders_box = QGroupBox("GIF Settings")
        grid = QGridLayout(sliders_box)
        grid.setColumnStretch(1, 1)

        self.start_slider, self.start_label = self._add_slider_row(
            grid, 0, "Start time"
        )
        self.length_slider, self.length_label = self._add_slider_row(
            grid, 1, "Length"
        )
        self.fps_slider, self.fps_label = self._add_slider_row(
            grid, 2, "Frame rate",
            minimum=GIF_MIN_FPS, maximum=GIF_MAX_FPS, value=GIF_DEFAULT_FPS,
            suffix=" fps",
        )
        self.width_slider, self.width_label = self._add_slider_row(
            grid, 3, "Width",
            minimum=GIF_MIN_WIDTH, maximum=GIF_MAX_WIDTH, value=GIF_DEFAULT_WIDTH,
            suffix=" px",
        )

        self.keep_original_width_checkbox = QCheckBox("Keep original")
        self.keep_original_width_checkbox.setCursor(Qt.PointingHandCursor)
        self.keep_original_width_checkbox.toggled.connect(self.on_keep_original_width_toggled)
        grid.addWidget(self.keep_original_width_checkbox, 3, 3)

        layout.addWidget(sliders_box)

        self.generate_button = QPushButton("Generate Preview")
        self.generate_button.setObjectName("ConvertButton")
        self.generate_button.setCursor(Qt.PointingHandCursor)
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self.on_generate_clicked)
        layout.addWidget(self.generate_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("NiceProgressBar")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        result_row = QHBoxLayout()
        self.preview_movie_label = QLabel()
        self.preview_movie_label.setFixedSize(PREVIEW_PLACEHOLDER_WIDTH, PREVIEW_PLACEHOLDER_HEIGHT)
        self.preview_movie_label.setAlignment(Qt.AlignCenter)
        self.preview_movie_label.setObjectName("PreviewBox")
        result_row.addWidget(self.preview_movie_label)

        result_col = QVBoxLayout()
        self.size_label = QLabel("")
        self.size_label.setStyleSheet("font-weight: 700;")
        result_col.addWidget(self.size_label)

        buttons_row = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("DownloadAllButton")
        self.save_button.setToolTip("Saves to same location file was found")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save_clicked)
        buttons_row.addWidget(self.save_button)

        self.save_to_button = QPushButton("Save To")
        self.save_to_button.setToolTip("Choose a location and file name to save to")
        self.save_to_button.setCursor(Qt.PointingHandCursor)
        self.save_to_button.setEnabled(False)
        self.save_to_button.clicked.connect(self.on_save_to_clicked)
        buttons_row.addWidget(self.save_to_button)
        buttons_row.addStretch()

        result_col.addLayout(buttons_row)
        result_col.addStretch()
        result_row.addLayout(result_col, stretch=1)

        layout.addLayout(result_row)

        log_label = QLabel("Log")
        log_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(log_label)

        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("LogBox")
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(120)
        self.log_box.setPlaceholderText("Conversion activity will show up here…")
        layout.addWidget(self.log_box)

        layout.addStretch()

    def _log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.appendPlainText(f"[{timestamp}] {message}")

    def _add_slider_row(self, grid, row, name, minimum=0, maximum=100, value=0, suffix=""):
        name_label = QLabel(name)
        grid.addWidget(name_label, row, 0)

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(minimum)
        slider.setMaximum(maximum)
        slider.setValue(value)
        grid.addWidget(slider, row, 1)

        value_label = QLabel(f"{value}{suffix}")
        value_label.setMinimumWidth(70)
        grid.addWidget(value_label, row, 2)

        slider.valueChanged.connect(
            lambda v, lbl=value_label, sfx=suffix: lbl.setText(f"{v}{sfx}")
        )
        return slider, value_label

    # -- Video selection ------------------------------------------------

    def choose_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a video", "", "MP4 Video (*.mp4)"
        )
        if not path:
            return

        try:
            clip = VideoFileClip(path)
            duration = clip.duration
            clip.close()
        except Exception as exc:  # noqa: BLE001
            self._log(f"ERROR opening {os.path.basename(path)}: {exc}")
            show_error(self, "Couldn't open video", str(exc))
            return

        self.video_path = path
        self.video_duration = duration
        self.file_label.setText(
            f"{os.path.basename(path)} — {duration:.1f}s"
        )

        try:
            source_size = os.path.getsize(path)
            size_text = _format_file_size(source_size)
        except OSError:
            size_text = "unknown size"
        self._log(f"Loaded {os.path.basename(path)} — {duration:.1f}s, {size_text}")

        capped_length = min(duration, GIF_MAX_LENGTH_SECONDS)
        self.start_slider.setMaximum(max(int(duration) - 1, 0))
        self.start_slider.setValue(0)
        self.length_slider.setMaximum(max(int(capped_length), 1))
        self.length_slider.setValue(max(int(capped_length), 1))
        self.start_label.setText("0")
        self.length_label.setText(str(self.length_slider.value()))

        self.generate_button.setEnabled(True)
        self._reset_result()

    def _reset_result(self):
        self.size_label.setText("")
        self.save_button.setEnabled(False)
        self.save_to_button.setEnabled(False)
        self.preview_movie_label.clear()
        self.preview_movie_label.setFixedSize(PREVIEW_PLACEHOLDER_WIDTH, PREVIEW_PLACEHOLDER_HEIGHT)
        if self.temp_gif_path and os.path.exists(self.temp_gif_path):
            try:
                os.remove(self.temp_gif_path)
            except OSError:
                pass
        self.temp_gif_path = None

    # -- Generate preview -------------------------------------------------

    def on_generate_clicked(self):
        if not self.video_path:
            return

        self._reset_result()
        self.generate_button.setEnabled(False)
        self.progress_bar.show()

        start = self.start_slider.value()
        length = self.length_slider.value()
        fps = self.fps_slider.value()
        width = None if self.keep_original_width_checkbox.isChecked() else self.width_slider.value()

        width_desc = "original" if width is None else f"{width}px wide"
        self._log(
            f"Converting {os.path.basename(self.video_path)} — "
            f"start={start}s, length={length}s, {fps} fps, {width_desc}"
        )

        fd, output_path = tempfile.mkstemp(suffix=".gif")
        os.close(fd)

        worker = GifConversionWorker(self.video_path, start, length, fps, width, output_path)
        worker.signals.finished.connect(self.on_generate_finished)
        worker.signals.failed.connect(self.on_generate_failed)
        self.main_window.thread_pool.start(worker)

    def on_generate_finished(self, temp_path, size_bytes):
        self.progress_bar.hide()
        self.generate_button.setEnabled(True)
        self.temp_gif_path = temp_path
        self.size_label.setText(f"Output file size: {_format_file_size(size_bytes)}")
        self._log(
            f"Converted {os.path.basename(self.video_path)} — "
            f"output size: {_format_file_size(size_bytes)}"
        )

        # Scale the preview box to fit the GIF's real aspect ratio so the
        # whole frame is always visible, whatever width was chosen.
        display_size = QSize(PREVIEW_BOX_MAX_WIDTH, PREVIEW_BOX_MAX_HEIGHT)
        try:
            with Image.open(temp_path) as gif_image:
                gif_w, gif_h = gif_image.size
            if gif_w and gif_h:
                scale = min(PREVIEW_BOX_MAX_WIDTH / gif_w, PREVIEW_BOX_MAX_HEIGHT / gif_h)
                display_size = QSize(max(int(gif_w * scale), 1), max(int(gif_h * scale), 1))
        except Exception:
            pass  # fall back to the default box size

        self.preview_movie_label.setFixedSize(display_size)

        movie = QMovie(temp_path)
        movie.setScaledSize(display_size)
        self.preview_movie_label.setMovie(movie)
        movie.start()
        self._preview_movie = movie  # keep a reference so it isn't garbage collected

        self.save_button.setEnabled(True)
        self.save_to_button.setEnabled(True)

    def on_generate_failed(self, error_message):
        self.progress_bar.hide()
        self.generate_button.setEnabled(True)
        self._log(f"ERROR converting {os.path.basename(self.video_path)}: {error_message}")
        show_error(self, "GIF generation failed", error_message)

    def on_keep_original_width_toggled(self, checked):
        self.width_slider.setEnabled(not checked)
        if checked:
            self.width_label.setText("Original")
        else:
            self.width_label.setText(f"{self.width_slider.value()} px")

    # -- Save --------------------------------------------------------------

    def _default_gif_path(self):
        base_name = os.path.splitext(os.path.basename(self.video_path))[0]
        return os.path.join(os.path.dirname(self.video_path), base_name + ".gif")

    def on_save_clicked(self):
        self._save_to(self._default_gif_path())

    def on_save_to_clicked(self):
        chosen_path, _ = QFileDialog.getSaveFileName(
            self, "Save GIF As", self._default_gif_path(), "GIF Image (*.gif)"
        )
        if chosen_path:
            self._save_to(chosen_path)

    def _save_to(self, destination):
        if not self.temp_gif_path or not self.video_path:
            return
        try:
            shutil.copy2(self.temp_gif_path, destination)
        except Exception as exc:  # noqa: BLE001
            self._log(f"ERROR saving to {destination}: {exc}")
            show_error(self, "Save failed", str(exc))
            return

        self._log(f"Saved {destination}")
        QMessageBox.information(self, "Saved", f"Saved {os.path.basename(destination)}")

        if self.main_window.delete_originals:
            deleted, delete_error = self.main_window._delete_original(self.video_path)
            if deleted:
                self._log(f"Deleted original file: {os.path.basename(self.video_path)}")
                QMessageBox.information(
                    self, "Original File Deleted",
                    f"Deleted original file:\n{os.path.basename(self.video_path)}",
                )
            elif delete_error:
                self._log(f"ERROR deleting original file: {delete_error}")
                show_error(
                    self, "Couldn't delete original",
                    f"Saved the GIF, but couldn't delete the original file:\n{delete_error}",
                )


# ---------------------------------------------------------------------------
# GIF Optimiser tab
# ---------------------------------------------------------------------------

class GifOptimiserTab(QWidget):
    """Load an existing GIF, preview it, and shrink its size/quality with a
    compression slider - plus one-click presets sized for Discord's actual
    emoji (256KB) and sticker (320x320px, 512KB) limits."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.source_path = None
        self.original_size_bytes = None
        self.temp_output_path = None
        self._preview_movie = None

        self._build_ui()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        if Image is None or ImageSequence is None:
            warning = QLabel("GIF Optimiser needs Pillow. Run: pip install Pillow")
            warning.setWordWrap(True)
            layout.addWidget(warning)
            layout.addStretch()
            return

        open_button = QPushButton("Open GIF…")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.clicked.connect(self.choose_gif)
        layout.addWidget(open_button)

        self.file_label = QLabel("No GIF selected")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        preview_row = QHBoxLayout()
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(PREVIEW_PLACEHOLDER_WIDTH, PREVIEW_PLACEHOLDER_HEIGHT)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setObjectName("PreviewBox")
        preview_row.addWidget(self.preview_label)

        info_col = QVBoxLayout()
        self.size_label = QLabel("")
        self.size_label.setStyleSheet("font-weight: 700;")
        self.size_label.setWordWrap(True)
        info_col.addWidget(self.size_label)
        info_col.addStretch()
        preview_row.addLayout(info_col, stretch=1)
        layout.addLayout(preview_row)

        self.resize_options = ResizeOptionsWidget()
        self.resize_options.setToolTip(
            "Resize the output. With a size chosen, the compression slider "
            "controls quality rather than dimensions."
        )
        layout.addWidget(self.resize_options)

        settings_box = QGroupBox("GIF Optimiser")
        grid = QGridLayout(settings_box)
        grid.setColumnStretch(1, 1)
        self.compression_slider, self.compression_label = self._add_slider_row(
            grid, 0, "Compression",
            minimum=GIF_OPT_MIN_COMPRESSION, maximum=GIF_OPT_MAX_COMPRESSION,
            value=GIF_OPT_DEFAULT_COMPRESSION, suffix="%",
        )
        layout.addWidget(settings_box)

        self.optimise_button = QPushButton("Optimise")
        self.optimise_button.setObjectName("ConvertButton")
        self.optimise_button.setCursor(Qt.PointingHandCursor)
        self.optimise_button.setEnabled(False)
        self.optimise_button.clicked.connect(self.on_optimise_clicked)
        layout.addWidget(self.optimise_button)

        target_size_box = QGroupBox("Target Size")
        target_grid = QGridLayout(target_size_box)
        target_grid.setSpacing(6)

        self.target_size_buttons = []

        def add_target_button(row, col, label, tooltip, target_bytes, target_square_size=None, is_custom=False):
            button = QPushButton(label)
            button.setCursor(Qt.PointingHandCursor)
            button.setEnabled(False)
            if tooltip:
                button.setToolTip(tooltip)
            if is_custom:
                button.clicked.connect(self.on_custom_target_clicked)
            else:
                button.clicked.connect(
                    lambda checked=False, tb=target_bytes, tsq=target_square_size: self.on_preset_clicked(tb, tsq)
                )
            target_grid.addWidget(button, row, col)
            self.target_size_buttons.append(button)
            return button

        self.emoji_button = add_target_button(
            0, 0, "Discord Emoji",
            f"Fits within {DISCORD_EMOJI_SIZE_PX}x{DISCORD_EMOJI_SIZE_PX}px "
            f"and under {DISCORD_EMOJI_MAX_BYTES // 1024}KB",
            DISCORD_EMOJI_MAX_BYTES, DISCORD_EMOJI_SIZE_PX,
        )
        self.sticker_button = add_target_button(
            0, 1, "Discord Sticker",
            f"Fits exactly {DISCORD_STICKER_SIZE_PX}x{DISCORD_STICKER_SIZE_PX}px "
            f"and under {DISCORD_STICKER_MAX_BYTES // 1024}KB",
            DISCORD_STICKER_MAX_BYTES, DISCORD_STICKER_SIZE_PX,
        )

        # Generic "shrink to roughly this many MiB" presets (no forced
        # square canvas - width/height stay proportional), 4 per row,
        # continuing on from the two Discord buttons above.
        position = 2
        for size_mib in TARGET_SIZE_PRESETS_MIB:
            row, col = divmod(position, 4)
            add_target_button(
                row, col, f"{size_mib} MiB", f"Shrinks to fit under {size_mib} MiB",
                size_mib * MIB,
            )
            position += 1

        row, col = divmod(position, 4)
        add_target_button(row, col, "Custom…", "Choose your own target size", None, is_custom=True)

        layout.addWidget(target_size_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("NiceProgressBar")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        buttons_row = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("DownloadAllButton")
        self.save_button.setToolTip("Saves to same location file was found")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save_clicked)
        buttons_row.addWidget(self.save_button)

        self.save_to_button = QPushButton("Save To")
        self.save_to_button.setToolTip("Choose a location and file name to save to")
        self.save_to_button.setCursor(Qt.PointingHandCursor)
        self.save_to_button.setEnabled(False)
        self.save_to_button.clicked.connect(self.on_save_to_clicked)
        buttons_row.addWidget(self.save_to_button)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        layout.addStretch()

    def _add_slider_row(self, grid, row, name, minimum=0, maximum=100, value=0, suffix=""):
        name_label = QLabel(name)
        grid.addWidget(name_label, row, 0)

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(minimum)
        slider.setMaximum(maximum)
        slider.setValue(value)
        grid.addWidget(slider, row, 1)

        value_label = QLabel(f"{value}{suffix}")
        value_label.setMinimumWidth(70)
        grid.addWidget(value_label, row, 2)

        slider.valueChanged.connect(
            lambda v, lbl=value_label, sfx=suffix: lbl.setText(f"{v}{sfx}")
        )
        return slider, value_label

    def _scaled_preview_size(self, width, height):
        scale = min(PREVIEW_BOX_MAX_WIDTH / width, PREVIEW_BOX_MAX_HEIGHT / height)
        return QSize(max(int(width * scale), 1), max(int(height * scale), 1))

    def _show_preview(self, path):
        try:
            with Image.open(path) as im:
                width, height = im.size
        except Exception:
            width, height = 200, 150

        display_size = self._scaled_preview_size(width, height)
        self.preview_label.setFixedSize(display_size)

        movie = QMovie(path)
        movie.setScaledSize(display_size)
        self.preview_label.setMovie(movie)
        movie.start()
        self._preview_movie = movie  # keep a reference so it isn't garbage collected

    # -- GIF selection ------------------------------------------------

    def choose_gif(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a GIF", "", "GIF Image (*.gif)"
        )
        if not path:
            return

        try:
            original_size = os.path.getsize(path)
            with Image.open(path) as im:
                width, height = im.size
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Couldn't open GIF", str(exc))
            return

        self.source_path = path
        self.original_size_bytes = original_size
        self.temp_output_path = None
        self.file_label.setText(
            f"{os.path.basename(path)} — {width}x{height}, {_format_file_size(original_size)}"
        )
        self.size_label.setText(f"Original size: {_format_file_size(original_size)}")
        self._show_preview(path)

        self.optimise_button.setEnabled(True)
        for button in self.target_size_buttons:
            button.setEnabled(True)
        self.save_button.setEnabled(False)
        self.save_to_button.setEnabled(False)

    # -- Optimise (manual slider) ------------------------------------------

    def on_optimise_clicked(self):
        if not self.source_path:
            return
        self._set_controls_enabled(False)
        self.progress_bar.show()

        level = self.compression_slider.value()
        fd, output_path = tempfile.mkstemp(suffix=".gif")
        os.close(fd)

        worker = GifOptimizeWorker(self.source_path, output_path, level,
                                   resize_spec=self.resize_options.spec())
        worker.signals.finished.connect(self.on_optimise_finished)
        worker.signals.failed.connect(self.on_optimise_failed)
        self.main_window.thread_pool.start(worker)

    def on_optimise_finished(self, output_path, size_bytes):
        self.progress_bar.hide()
        self._set_controls_enabled(True)
        self.temp_output_path = output_path
        self._show_preview(output_path)
        self._update_size_label(size_bytes)
        self.save_button.setEnabled(True)
        self.save_to_button.setEnabled(True)

    def on_optimise_failed(self, error_message):
        self.progress_bar.hide()
        self._set_controls_enabled(True)
        show_error(self, "Optimisation failed", error_message)

    # -- Target size presets -------------------------------------------------

    def on_custom_target_clicked(self):
        size_mib, ok = QInputDialog.getDouble(
            self, "Custom Target Size", "Target size (MiB):",
            10.0, 0.05, 2048.0, 2,
        )
        if not ok:
            return
        self.on_preset_clicked(int(size_mib * MIB), None)

    def on_preset_clicked(self, target_bytes, target_square_size):
        if not self.source_path:
            return
        self._set_controls_enabled(False)
        self.progress_bar.show()

        fd, output_path = tempfile.mkstemp(suffix=".gif")
        os.close(fd)

        worker = GifPresetWorker(self.source_path, output_path, target_bytes, target_square_size,
                                 resize_spec=self.resize_options.spec())
        worker.signals.finished.connect(self.on_preset_finished)
        worker.signals.failed.connect(self.on_preset_failed)
        self.main_window.thread_pool.start(worker)

    def on_preset_finished(self, output_path, size_bytes, level_used, met_target):
        self.progress_bar.hide()
        self._set_controls_enabled(True)
        self.temp_output_path = output_path

        self.compression_slider.blockSignals(True)
        self.compression_slider.setValue(level_used)
        self.compression_slider.blockSignals(False)
        self.compression_label.setText(f"{level_used}%")

        self._show_preview(output_path)
        self._update_size_label(size_bytes)
        self.save_button.setEnabled(True)
        self.save_to_button.setEnabled(True)

        if not met_target:
            show_error(
                self, "Couldn't fully meet target",
                f"Even at maximum compression, the result is "
                f"{_format_file_size(size_bytes)}, which is still over the "
                f"target size. This GIF may have too many frames or too much "
                f"detail to fit - consider trimming it first.",
            )

    def on_preset_failed(self, error_message):
        self.progress_bar.hide()
        self._set_controls_enabled(True)
        show_error(self, "Optimisation failed", error_message)

    def _set_controls_enabled(self, enabled):
        self.optimise_button.setEnabled(enabled)
        for button in self.target_size_buttons:
            button.setEnabled(enabled)

    def _update_size_label(self, size_bytes):
        reduction = 0
        if self.original_size_bytes:
            reduction = 100 * (1 - size_bytes / self.original_size_bytes)
        change_word = "smaller" if reduction >= 0 else "larger"
        self.size_label.setText(
            f"Optimized size: {_format_file_size(size_bytes)} "
            f"(was {_format_file_size(self.original_size_bytes)}, "
            f"{abs(reduction):.0f}% {change_word})"
        )

    # -- Save ----------------------------------------------------------------

    def _default_optimized_path(self):
        # "-optimized" suffix rather than the original name, so Save
        # doesn't silently overwrite the source GIF.
        base_name = os.path.splitext(os.path.basename(self.source_path))[0]
        return os.path.join(os.path.dirname(self.source_path), base_name + "-optimized.gif")

    def on_save_clicked(self):
        self._save_to(self._default_optimized_path())

    def on_save_to_clicked(self):
        chosen_path, _ = QFileDialog.getSaveFileName(
            self, "Save GIF As", self._default_optimized_path(), "GIF Image (*.gif)"
        )
        if chosen_path:
            self._save_to(chosen_path)

    def _save_to(self, destination):
        if not self.temp_output_path or not self.source_path:
            return
        try:
            shutil.copy2(self.temp_output_path, destination)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Save failed", str(exc))
            return

        QMessageBox.information(self, "Saved", f"Saved {os.path.basename(destination)}")


# ---------------------------------------------------------------------------
# Image Optimiser tab - same idea (and same Target Size presets) as the GIF
# Optimiser, but for a single still image instead of an animated GIF
# ---------------------------------------------------------------------------

def _build_optimized_image(source_path, compression_level, output_path,
                           target_square_size=None, resize_spec=None):
    """Re-encodes a still image at a given compression level (1-100, higher
    = more compression/smaller/lower quality). If target_square_size is
    given, the image is scaled to fit within that square and centered on a
    transparent canvas of exactly that size (used by the Discord presets),
    same as _build_optimized_gif.

    Images that need transparency (either the source has an alpha channel,
    or padding onto a square canvas introduced some) are saved as PNG,
    losslessly, and shrink primarily by resizing. Fully opaque images are
    saved as JPEG, where `compression_level` also lowers the JPEG quality -
    a real, reliable size/quality tradeoff for photos. Because the output
    format depends on the image rather than being fixed up front, this
    returns the actual output path used (its extension may differ from
    output_path's)."""
    with Image.open(source_path) as im:
        has_alpha = ("transparency" in im.info) or (im.mode in ("RGBA", "LA", "PA"))
        source = im.convert("RGBA").copy()

    if target_square_size is not None:
        resize_spec = None  # the preset's own size takes precedence
    if resize_spec:
        source = resize_to_spec(source, resize_spec)

    needs_transparency = (has_alpha or target_square_size is not None
                          or spec_adds_transparency(resize_spec))

    # Higher compression -> smaller scale (and, for opaque JPEGs, lower
    # quality too) - same curve as the GIF optimizer for consistency.
    scale = max(1.0 - (compression_level / 100) * 0.7, 0.15)
    if resize_spec:
        # An exact size was asked for, so keep it and let compression work
        # through JPEG quality instead.
        scale = 1.0
    w, h = source.size

    if target_square_size:
        content_box = max(int(target_square_size * scale), 16)
        ratio = min(content_box / w, content_box / h)
        new_w, new_h = max(1, int(w * ratio)), max(1, int(h * ratio))
        resized = source.resize((new_w, new_h), RESAMPLE_LANCZOS)
        canvas = Image.new("RGBA", (target_square_size, target_square_size), (0, 0, 0, 0))
        canvas.paste(resized, ((target_square_size - new_w) // 2, (target_square_size - new_h) // 2), resized)
        output_image = canvas
    else:
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        output_image = source.resize((new_w, new_h), RESAMPLE_LANCZOS)

    base_path = os.path.splitext(output_path)[0]

    if needs_transparency:
        final_path = base_path + ".png"
        output_image.save(final_path, format="PNG", optimize=True, compress_level=9)
    else:
        quality = max(5, round(95 - (compression_level / 100) * 90))
        final_path = base_path + ".jpg"
        output_image.convert("RGB").save(final_path, format="JPEG", quality=quality, optimize=True)

    return final_path


class ImageOptimizeSignals(QObject):
    finished = pyqtSignal(str, int)  # output_path, size_bytes
    failed = pyqtSignal(str)


class ImageOptimizeWorker(QRunnable):
    """Runs the optimizer once, at whatever compression level was chosen."""

    def __init__(self, source_path, output_path, compression_level,
                 target_square_size=None, resize_spec=None):
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.compression_level = compression_level
        self.target_square_size = target_square_size
        self.resize_spec = resize_spec
        self.signals = ImageOptimizeSignals()

    def run(self):
        try:
            final_path = _build_optimized_image(
                self.source_path, self.compression_level, self.output_path,
                target_square_size=self.target_square_size,
                resize_spec=self.resize_spec,
            )
            size = os.path.getsize(final_path)
            self.signals.finished.emit(final_path, size)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


class ImagePresetSignals(QObject):
    finished = pyqtSignal(str, int, int, bool)  # output_path, size_bytes, level_used, met_target
    failed = pyqtSignal(str)


class ImagePresetWorker(QRunnable):
    """Searches increasing compression levels until the result fits under
    target_bytes (and, for stickers, an exact square canvas), then stops -
    same approach as GifPresetWorker."""

    def __init__(self, source_path, output_path, target_bytes, target_square_size,
                 resize_spec=None):
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.target_bytes = target_bytes
        self.target_square_size = target_square_size
        self.resize_spec = resize_spec
        self.signals = ImagePresetSignals()

    def run(self):
        try:
            chosen_level = IMAGE_OPT_MAX_COMPRESSION
            met_target = False
            final_path = self.output_path
            for level in range(10, IMAGE_OPT_MAX_COMPRESSION + 1, 10):
                final_path = _build_optimized_image(
                    self.source_path, level, self.output_path,
                    target_square_size=self.target_square_size,
                    resize_spec=self.resize_spec,
                )
                size = os.path.getsize(final_path)
                chosen_level = level
                if size <= self.target_bytes:
                    met_target = True
                    break

            size = os.path.getsize(final_path)
            self.signals.finished.emit(final_path, size, chosen_level, met_target)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


class ImageOptimiserTab(QWidget):
    """Load an existing still image, preview it, and shrink its size with a
    compression slider - plus the exact same one-click Target Size presets
    as the GIF Optimiser (Discord Emoji/Sticker, MiB presets, and Custom)."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.source_path = None
        self.original_size_bytes = None
        self.temp_output_path = None

        self._build_ui()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        if Image is None:
            warning = QLabel("Image Optimiser needs Pillow. Run: pip install Pillow")
            warning.setWordWrap(True)
            layout.addWidget(warning)
            layout.addStretch()
            return

        open_button = QPushButton("Open Image…")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.clicked.connect(self.choose_image)
        layout.addWidget(open_button)

        self.file_label = QLabel("No image selected")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        preview_row = QHBoxLayout()
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(PREVIEW_PLACEHOLDER_WIDTH, PREVIEW_PLACEHOLDER_HEIGHT)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setObjectName("PreviewBox")
        preview_row.addWidget(self.preview_label)

        info_col = QVBoxLayout()
        self.size_label = QLabel("")
        self.size_label.setStyleSheet("font-weight: 700;")
        self.size_label.setWordWrap(True)
        info_col.addWidget(self.size_label)
        info_col.addStretch()
        preview_row.addLayout(info_col, stretch=1)
        layout.addLayout(preview_row)

        self.resize_options = ResizeOptionsWidget()
        self.resize_options.setToolTip(
            "Resize the output. With a size chosen, the compression slider "
            "controls quality rather than dimensions."
        )
        layout.addWidget(self.resize_options)

        settings_box = QGroupBox("Image Optimiser")
        grid = QGridLayout(settings_box)
        grid.setColumnStretch(1, 1)
        self.compression_slider, self.compression_label = self._add_slider_row(
            grid, 0, "Compression",
            minimum=IMAGE_OPT_MIN_COMPRESSION, maximum=IMAGE_OPT_MAX_COMPRESSION,
            value=IMAGE_OPT_DEFAULT_COMPRESSION, suffix="%",
        )
        layout.addWidget(settings_box)

        self.optimise_button = QPushButton("Optimise")
        self.optimise_button.setObjectName("ConvertButton")
        self.optimise_button.setCursor(Qt.PointingHandCursor)
        self.optimise_button.setEnabled(False)
        self.optimise_button.clicked.connect(self.on_optimise_clicked)
        layout.addWidget(self.optimise_button)

        target_size_box = QGroupBox("Target Size")
        target_grid = QGridLayout(target_size_box)
        target_grid.setSpacing(6)

        self.target_size_buttons = []

        def add_target_button(row, col, label, tooltip, target_bytes, target_square_size=None, is_custom=False):
            button = QPushButton(label)
            button.setCursor(Qt.PointingHandCursor)
            button.setEnabled(False)
            if tooltip:
                button.setToolTip(tooltip)
            if is_custom:
                button.clicked.connect(self.on_custom_target_clicked)
            else:
                button.clicked.connect(
                    lambda checked=False, tb=target_bytes, tsq=target_square_size: self.on_preset_clicked(tb, tsq)
                )
            target_grid.addWidget(button, row, col)
            self.target_size_buttons.append(button)
            return button

        self.emoji_button = add_target_button(
            0, 0, "Discord Emoji",
            f"Fits within {DISCORD_EMOJI_SIZE_PX}x{DISCORD_EMOJI_SIZE_PX}px "
            f"and under {DISCORD_EMOJI_MAX_BYTES // 1024}KB",
            DISCORD_EMOJI_MAX_BYTES, DISCORD_EMOJI_SIZE_PX,
        )
        self.sticker_button = add_target_button(
            0, 1, "Discord Sticker",
            f"Fits exactly {DISCORD_STICKER_SIZE_PX}x{DISCORD_STICKER_SIZE_PX}px "
            f"and under {DISCORD_STICKER_MAX_BYTES // 1024}KB",
            DISCORD_STICKER_MAX_BYTES, DISCORD_STICKER_SIZE_PX,
        )

        position = 2
        for size_mib in TARGET_SIZE_PRESETS_MIB:
            row, col = divmod(position, 4)
            add_target_button(
                row, col, f"{size_mib} MiB", f"Shrinks to fit under {size_mib} MiB",
                size_mib * MIB,
            )
            position += 1

        row, col = divmod(position, 4)
        add_target_button(row, col, "Custom…", "Choose your own target size", None, is_custom=True)

        layout.addWidget(target_size_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("NiceProgressBar")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        buttons_row = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("DownloadAllButton")
        self.save_button.setToolTip("Saves to same location file was found")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save_clicked)
        buttons_row.addWidget(self.save_button)

        self.save_to_button = QPushButton("Save To")
        self.save_to_button.setToolTip("Choose a location and file name to save to")
        self.save_to_button.setCursor(Qt.PointingHandCursor)
        self.save_to_button.setEnabled(False)
        self.save_to_button.clicked.connect(self.on_save_to_clicked)
        buttons_row.addWidget(self.save_to_button)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        layout.addStretch()

    def _add_slider_row(self, grid, row, name, minimum=0, maximum=100, value=0, suffix=""):
        name_label = QLabel(name)
        grid.addWidget(name_label, row, 0)

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(minimum)
        slider.setMaximum(maximum)
        slider.setValue(value)
        grid.addWidget(slider, row, 1)

        value_label = QLabel(f"{value}{suffix}")
        value_label.setMinimumWidth(70)
        grid.addWidget(value_label, row, 2)

        slider.valueChanged.connect(
            lambda v, lbl=value_label, sfx=suffix: lbl.setText(f"{v}{sfx}")
        )
        return slider, value_label

    def _scaled_preview_size(self, width, height):
        scale = min(PREVIEW_BOX_MAX_WIDTH / width, PREVIEW_BOX_MAX_HEIGHT / height)
        return QSize(max(int(width * scale), 1), max(int(height * scale), 1))

    def _show_preview(self, path):
        try:
            with Image.open(path) as im:
                width, height = im.size
        except Exception:
            width, height = 200, 150

        display_size = self._scaled_preview_size(width, height)
        self.preview_label.setFixedSize(display_size)

        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.preview_label.setPixmap(
                pixmap.scaled(display_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    # -- Image selection ------------------------------------------------

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tiff *.tif)"
        )
        if not path:
            return

        try:
            original_size = os.path.getsize(path)
            with Image.open(path) as im:
                width, height = im.size
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Couldn't open image", str(exc))
            return

        self.source_path = path
        self.original_size_bytes = original_size
        self.temp_output_path = None
        self.file_label.setText(
            f"{os.path.basename(path)} — {width}x{height}, {_format_file_size(original_size)}"
        )
        self.size_label.setText(f"Original size: {_format_file_size(original_size)}")
        self._show_preview(path)

        self.optimise_button.setEnabled(True)
        for button in self.target_size_buttons:
            button.setEnabled(True)
        self.save_button.setEnabled(False)
        self.save_to_button.setEnabled(False)

    # -- Optimise (manual slider) ------------------------------------------

    def on_optimise_clicked(self):
        if not self.source_path:
            return
        self._set_controls_enabled(False)
        self.progress_bar.show()

        level = self.compression_slider.value()
        fd, output_path = tempfile.mkstemp(suffix=".img")
        os.close(fd)

        worker = ImageOptimizeWorker(self.source_path, output_path, level,
                                   resize_spec=self.resize_options.spec())
        worker.signals.finished.connect(self.on_optimise_finished)
        worker.signals.failed.connect(self.on_optimise_failed)
        self.main_window.thread_pool.start(worker)

    def on_optimise_finished(self, output_path, size_bytes):
        self.progress_bar.hide()
        self._set_controls_enabled(True)
        self.temp_output_path = output_path
        self._show_preview(output_path)
        self._update_size_label(size_bytes)
        self.save_button.setEnabled(True)
        self.save_to_button.setEnabled(True)

    def on_optimise_failed(self, error_message):
        self.progress_bar.hide()
        self._set_controls_enabled(True)
        show_error(self, "Optimisation failed", error_message)

    # -- Target size presets -------------------------------------------------

    def on_custom_target_clicked(self):
        size_mib, ok = QInputDialog.getDouble(
            self, "Custom Target Size", "Target size (MiB):",
            10.0, 0.05, 2048.0, 2,
        )
        if not ok:
            return
        self.on_preset_clicked(int(size_mib * MIB), None)

    def on_preset_clicked(self, target_bytes, target_square_size):
        if not self.source_path:
            return
        self._set_controls_enabled(False)
        self.progress_bar.show()

        fd, output_path = tempfile.mkstemp(suffix=".img")
        os.close(fd)

        worker = ImagePresetWorker(self.source_path, output_path, target_bytes, target_square_size,
                                 resize_spec=self.resize_options.spec())
        worker.signals.finished.connect(self.on_preset_finished)
        worker.signals.failed.connect(self.on_preset_failed)
        self.main_window.thread_pool.start(worker)

    def on_preset_finished(self, output_path, size_bytes, level_used, met_target):
        self.progress_bar.hide()
        self._set_controls_enabled(True)
        self.temp_output_path = output_path

        self.compression_slider.blockSignals(True)
        self.compression_slider.setValue(level_used)
        self.compression_slider.blockSignals(False)
        self.compression_label.setText(f"{level_used}%")

        self._show_preview(output_path)
        self._update_size_label(size_bytes)
        self.save_button.setEnabled(True)
        self.save_to_button.setEnabled(True)

        if not met_target:
            show_error(
                self, "Couldn't fully meet target",
                f"Even at maximum compression, the result is "
                f"{_format_file_size(size_bytes)}, which is still over the "
                f"target size. This image may have too much detail to fit "
                f"at that size - consider a lower resolution source.",
            )

    def on_preset_failed(self, error_message):
        self.progress_bar.hide()
        self._set_controls_enabled(True)
        show_error(self, "Optimisation failed", error_message)

    def _set_controls_enabled(self, enabled):
        self.optimise_button.setEnabled(enabled)
        for button in self.target_size_buttons:
            button.setEnabled(enabled)

    def _update_size_label(self, size_bytes):
        reduction = 0
        if self.original_size_bytes:
            reduction = 100 * (1 - size_bytes / self.original_size_bytes)
        change_word = "smaller" if reduction >= 0 else "larger"
        self.size_label.setText(
            f"Optimized size: {_format_file_size(size_bytes)} "
            f"(was {_format_file_size(self.original_size_bytes)}, "
            f"{abs(reduction):.0f}% {change_word})"
        )

    # -- Save ----------------------------------------------------------------

    def _default_optimized_path(self):
        # "-optimized" suffix rather than the original name, so Save
        # doesn't silently overwrite the source image. Reuses whatever
        # extension the last conversion actually produced (PNG or JPEG).
        base_name = os.path.splitext(os.path.basename(self.source_path))[0]
        ext = os.path.splitext(self.temp_output_path)[1] if self.temp_output_path else ".png"
        return os.path.join(os.path.dirname(self.source_path), base_name + "-optimized" + ext)

    def on_save_clicked(self):
        self._save_to(self._default_optimized_path())

    def on_save_to_clicked(self):
        ext = os.path.splitext(self.temp_output_path)[1] if self.temp_output_path else ".png"
        filter_str = "JPEG Image (*.jpg)" if ext == ".jpg" else "PNG Image (*.png)"
        chosen_path, _ = QFileDialog.getSaveFileName(
            self, "Save Image As", self._default_optimized_path(), filter_str
        )
        if chosen_path:
            self._save_to(chosen_path)

    def _save_to(self, destination):
        if not self.temp_output_path or not self.source_path:
            return
        try:
            shutil.copy2(self.temp_output_path, destination)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Save failed", str(exc))
            return

        QMessageBox.information(self, "Saved", f"Saved {os.path.basename(destination)}")


# ---------------------------------------------------------------------------
# WebP to GIF tab - converts an animated (or still) WebP into a GIF, with
# live before/after previews and independent Quality / Frames / Width sliders
# ---------------------------------------------------------------------------

def _keep_every_for_frames_value(frames_value):
    """Maps the 10-100 'Frames' slider (percent of original frames to keep)
    to a keep-every-Nth-frame stride. 100 keeps every frame; lower values
    thin the animation out in coarser steps as they drop."""
    if frames_value >= 100:
        return 1
    return max(1, round(100 / max(frames_value, 1)))


def _build_gif_from_webp(source_path, output_path, quality, frames_value, width=None):
    """Reads every frame out of a WebP (animated or still) and re-encodes it
    as a GIF.

    - `quality` (1-100) controls the palette size and dithering: higher
      keeps more colors and smooths banding, lower shrinks the palette hard
      for a smaller file.
    - `frames_value` (10-100, percent) thins out how many of the original
      frames are kept, stretching each kept frame's duration so the overall
      playback speed stays the same.
    - `width`, if given, rescales every frame to that width, preserving
      aspect ratio.
    """
    frames = []
    durations = []
    with Image.open(source_path) as im:
        source_has_transparency = ("transparency" in im.info) or (im.mode in ("RGBA", "LA", "PA"))
        for frame in ImageSequence.Iterator(im):
            frames.append(frame.convert("RGBA").copy())
            durations.append(frame.info.get("duration", 100))

    if not frames:
        raise RuntimeError("No frames could be read from this WebP.")

    keep_every = _keep_every_for_frames_value(frames_value)
    colors = max(8, round(8 + (quality / 100) * 248))
    dither = DITHER_FLOYDSTEINBERG if quality >= 35 else DITHER_NONE

    frame_outs = []
    new_durations = []
    for i, (frame, duration) in enumerate(zip(frames, durations)):
        if i % keep_every != 0:
            continue

        frame_out = frame
        if width:
            w, h = frame.size
            ratio = width / w
            new_size = (max(int(width), 1), max(int(round(h * ratio)), 1))
            frame_out = frame.resize(new_size, RESAMPLE_LANCZOS)

        frame_outs.append(frame_out)
        new_durations.append(duration * keep_every)

    if not frame_outs:
        raise RuntimeError("No frames left after applying the Frames setting.")

    extra_save_kwargs = {}

    if source_has_transparency:
        # Same shared-palette trick used by _build_optimized_gif: GIF only
        # supports one transparent index, so transparent pixels are keyed to
        # a placeholder color before quantizing against a single shared
        # palette, keeping that index consistent across every frame.
        key_color = (255, 0, 255)
        keyed_frames = []
        for frame_out in frame_outs:
            keyed = Image.new("RGB", frame_out.size, key_color)
            keyed.paste(frame_out, mask=frame_out.split()[3])
            keyed_frames.append(keyed)

        base_quantized = keyed_frames[0].quantize(colors=colors, method=QUANTIZE_METHOD, dither=dither)
        processed = [base_quantized] + [
            kf.quantize(palette=base_quantized, dither=dither) for kf in keyed_frames[1:]
        ]

        transparency_index = _find_palette_index(base_quantized, key_color)
        if transparency_index is not None:
            extra_save_kwargs["transparency"] = transparency_index
    else:
        processed = [
            frame_out.convert("RGB").quantize(colors=colors, method=QUANTIZE_METHOD, dither=dither)
            for frame_out in frame_outs
        ]

    save_kwargs = dict(format="GIF", optimize=True, disposal=2, **extra_save_kwargs)
    if len(processed) > 1:
        save_kwargs.update(
            save_all=True, append_images=processed[1:],
            duration=new_durations, loop=0,
        )

    processed[0].save(output_path, **save_kwargs)


class WebpToGifSignals(QObject):
    finished = pyqtSignal(str, int)  # output_path, size_bytes
    failed = pyqtSignal(str)


class WebpToGifWorker(QRunnable):
    def __init__(self, source_path, output_path, quality, frames_value, width):
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.quality = quality
        self.frames_value = frames_value
        self.width = width
        self.signals = WebpToGifSignals()

    def run(self):
        try:
            _build_gif_from_webp(
                self.source_path, self.output_path, self.quality,
                self.frames_value, width=self.width,
            )
            size = os.path.getsize(self.output_path)
            self.signals.finished.emit(self.output_path, size)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


class WebpToGifPresetSignals(QObject):
    finished = pyqtSignal(str, int, int, bool)  # output_path, size_bytes, quality_used, met_target
    failed = pyqtSignal(str)


class WebpToGifPresetWorker(QRunnable):
    """Searches decreasing Quality levels (keeping Frames/Width as-is) until
    the result fits under target_bytes, then stops - same idea as
    GifPresetWorker, just sweeping the Quality knob instead of a single
    combined compression level."""

    def __init__(self, source_path, output_path, target_bytes, frames_value, width):
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.target_bytes = target_bytes
        self.frames_value = frames_value
        self.width = width
        self.signals = WebpToGifPresetSignals()

    def run(self):
        try:
            chosen_quality = WEBP_QUALITY_MIN
            met_target = False
            for quality in range(WEBP_QUALITY_MAX, WEBP_QUALITY_MIN - 1, -10):
                _build_gif_from_webp(
                    self.source_path, self.output_path, quality,
                    self.frames_value, width=self.width,
                )
                size = os.path.getsize(self.output_path)
                chosen_quality = quality
                if size <= self.target_bytes:
                    met_target = True
                    break

            size = os.path.getsize(self.output_path)
            self.signals.finished.emit(self.output_path, size, chosen_quality, met_target)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


class WebpCompletedRowWidget(QWidget):
    """One row in the WebP to GIF tab's Completed list: filename, a
    progress bar while converting, then a "Show Preview" button (loads
    this file into the tab's single shared preview box - only one file's
    GIF is ever actually being played back at a time) plus Save / Save To
    once the file is ready on disk."""

    def __init__(self, filename, tooltip_path, on_save, on_save_to, on_preview, parent=None):
        super().__init__(parent)
        self.setObjectName("RowWidget")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self.name_label = QLabel(filename)
        self.name_label.setToolTip(tooltip_path)
        self.name_label.setMinimumWidth(110)
        layout.addWidget(self.name_label, stretch=2)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("NiceProgressBar")
        self.progress_bar.setRange(0, 0)  # animated "busy" style
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        layout.addWidget(self.progress_bar, stretch=2)

        self.status_label = QLabel("")
        self.status_label.hide()
        layout.addWidget(self.status_label, stretch=2)

        self.preview_button = QPushButton("Show Preview")
        self.preview_button.setCursor(Qt.PointingHandCursor)
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(on_preview)
        layout.addWidget(self.preview_button)

        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("RowSaveButton")
        self.save_button.setToolTip("Saves to same location file was found")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(on_save)
        layout.addWidget(self.save_button)

        self.save_to_button = QPushButton("Save To")
        self.save_to_button.setObjectName("RowSaveToButton")
        self.save_to_button.setToolTip("Choose a location and file name to save to")
        self.save_to_button.setCursor(Qt.PointingHandCursor)
        self.save_to_button.setEnabled(False)
        self.save_to_button.clicked.connect(on_save_to)
        layout.addWidget(self.save_to_button)

    def mark_converted(self, status_text="Converted"):
        self.progress_bar.hide()
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet("color: #1fa851; font-weight: 700;")
        self.status_label.show()
        self.preview_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.save_to_button.setEnabled(True)

    def mark_failed(self, message):
        self.progress_bar.hide()
        self.status_label.setText(f"Failed: {message}")
        self.status_label.setStyleSheet("color: #d93025; font-weight: 700;")
        self.status_label.show()

    def flash_saved(self):
        """Brief visual acknowledgement after a save completes."""
        original = self.status_label.text()
        original_style = self.status_label.styleSheet()
        self.status_label.setText("Saved ✓")
        self.status_label.setStyleSheet("color: #007aff; font-weight: 700;")

        def _restore():
            self.status_label.setText(original)
            self.status_label.setStyleSheet(original_style)

        QTimer.singleShot(1200, _restore)


class WebpToGifTab(QWidget):
    """Batch-convert up to MAX_FILES WebPs (animated or still) into GIFs at
    once. Only one file's result is ever actually being previewed at a
    time - each row in Completed gets its own "Show Preview" button that
    loads that file into the tab's single shared preview box."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window

        # path -> {"item": QListWidgetItem, "widget": SelectedRowWidget}
        self.queued_items = {}
        # path -> {"item", "widget", "output_path", "output_size", "original_size"}
        self.completed_rows = {}
        # paths already handed to the thread pool
        self.submitted_paths = set()
        # paths whose conversion ended in failure (still shown in
        # Completed until Clear Completed is used)
        self.failed_paths = set()

        self._preview_movie = None

        self._build_ui()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        if Image is None or ImageSequence is None:
            warning = QLabel("WebP to GIF needs Pillow. Run: pip install Pillow")
            warning.setWordWrap(True)
            layout.addWidget(warning)
            layout.addStretch()
            return

        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        open_button = QPushButton("Open WebP(s)…")
        open_button.setObjectName("ConvertButton")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.clicked.connect(self.choose_webps)
        top_row.addWidget(open_button)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("MutedLabel")
        top_row.addWidget(self.status_label, stretch=1)
        layout.addLayout(top_row)

        # -- Row 1: the two file lists, side by side -------------------------
        files_row = QHBoxLayout()
        files_row.setSpacing(16)

        selected_box = QGroupBox("Selected Files")
        selected_layout = QVBoxLayout(selected_box)
        selected_layout.setContentsMargins(14, 14, 14, 14)
        self.selected_list = QListWidget()
        selected_layout.addWidget(self.selected_list)
        selected_box.setMinimumWidth(240)
        selected_box.setMaximumWidth(320)
        selected_box.setMinimumHeight(150)
        files_row.addWidget(selected_box)

        completed_box = QGroupBox("Completed")
        completed_layout = QVBoxLayout(completed_box)
        completed_layout.setContentsMargins(14, 14, 14, 14)
        self.completed_list = QListWidget()
        self.completed_list.setSpacing(3)
        completed_layout.addWidget(self.completed_list)
        completed_box.setMinimumHeight(150)
        files_row.addWidget(completed_box, stretch=1)

        layout.addLayout(files_row, stretch=1)

        # -- Row 2: settings + actions on the left, preview on the right -----
        # Settings on the left with the preview beside them, or stacked when
        # the window is too narrow for both - see _apply_responsive_layout.
        self.work_grid = QGridLayout()
        self.work_grid.setHorizontalSpacing(16)
        self.work_grid.setVerticalSpacing(14)

        self.work_left = QWidget()
        left_col = QVBoxLayout(self.work_left)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(14)

        settings_box = QGroupBox("GIF Settings")
        grid = QGridLayout(settings_box)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(1, 1)

        self.quality_slider, self.quality_label = self._add_slider_row(
            grid, 0, "Quality",
            minimum=WEBP_QUALITY_MIN, maximum=WEBP_QUALITY_MAX,
            value=WEBP_QUALITY_DEFAULT, suffix="%",
        )
        self.frames_slider, self.frames_label = self._add_slider_row(
            grid, 1, "Frames",
            minimum=WEBP_FRAMES_MIN, maximum=WEBP_FRAMES_MAX,
            value=WEBP_FRAMES_DEFAULT, suffix="%",
        )
        self.width_slider, self.width_label = self._add_slider_row(
            grid, 2, "Width",
            minimum=WEBP_WIDTH_MIN, maximum=WEBP_WIDTH_MAX,
            value=WEBP_WIDTH_DEFAULT, suffix=" px",
        )
        self.width_slider.setEnabled(False)

        self.keep_original_width_checkbox = QCheckBox("Keep original")
        self.keep_original_width_checkbox.setCursor(Qt.PointingHandCursor)
        self.keep_original_width_checkbox.setChecked(True)
        self.keep_original_width_checkbox.toggled.connect(self.on_keep_original_width_toggled)
        grid.addWidget(self.keep_original_width_checkbox, 2, 3)

        settings_hint = QLabel(
            "These settings apply to every file in Completed when you hit "
            "Convert or a Target Size preset."
        )
        settings_hint.setWordWrap(True)
        settings_hint.setObjectName("HintLabel")
        grid.addWidget(settings_hint, 3, 0, 1, 4)

        left_col.addWidget(settings_box)

        self.convert_button = QPushButton("Convert")
        self.convert_button.setObjectName("ConvertButton")
        self.convert_button.setCursor(Qt.PointingHandCursor)
        self.convert_button.setMinimumHeight(40)
        self.convert_button.clicked.connect(self.on_convert_clicked)
        left_col.addWidget(self.convert_button)

        target_size_box = QGroupBox("Target Size")
        target_grid = QGridLayout(target_size_box)
        target_grid.setContentsMargins(14, 16, 14, 16)
        target_grid.setHorizontalSpacing(8)
        target_grid.setVerticalSpacing(10)

        self.target_size_buttons = []
        for position, size_mib in enumerate(TARGET_SIZE_PRESETS_MIB):
            row, col = divmod(position, 5)
            button = QPushButton(f"{size_mib} MiB")
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(f"Shrinks to fit under {size_mib} MiB")
            button.clicked.connect(
                lambda checked=False, mib=size_mib: self.on_target_size_clicked(mib * MIB)
            )
            target_grid.addWidget(button, row, col)
            self.target_size_buttons.append(button)

        custom_position = len(TARGET_SIZE_PRESETS_MIB)
        row, col = divmod(custom_position, 5)
        custom_button = QPushButton("Custom…")
        custom_button.setCursor(Qt.PointingHandCursor)
        custom_button.setToolTip("Choose your own target size")
        custom_button.clicked.connect(self.on_custom_target_clicked)
        target_grid.addWidget(custom_button, row, col)
        self.target_size_buttons.append(custom_button)

        left_col.addWidget(target_size_box)
        left_col.addStretch()

        preview_box = QGroupBox("Preview")
        preview_col = QVBoxLayout(preview_box)
        preview_col.setContentsMargins(12, 16, 12, 16)
        preview_col.setSpacing(12)
        self.preview_caption = QLabel("Nothing previewed yet")
        self.preview_caption.setObjectName("MutedLabel")
        self.preview_caption.setWordWrap(True)
        self.preview_caption.setAlignment(Qt.AlignHCenter)
        preview_col.addWidget(self.preview_caption)
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(PREVIEW_PLACEHOLDER_WIDTH, PREVIEW_PLACEHOLDER_HEIGHT)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setObjectName("PreviewBox")
        self.preview_label.setText("Click \"Show Preview\"\non a completed file")
        preview_col.addWidget(self.preview_label, alignment=Qt.AlignHCenter)
        preview_col.addStretch()
        preview_box.setFixedWidth(PREVIEW_BOX_MAX_WIDTH + 30)
        preview_box.setMinimumHeight(PREVIEW_BOX_MAX_HEIGHT + 90)
        self.preview_box = preview_box
        self._stacked_layout = None
        self._apply_responsive_layout(stacked=False)

        layout.addLayout(self.work_grid)

        # -- Bottom actions --------------------------------------------------
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)
        self.clear_completed_button = QPushButton("Clear Completed")
        self.clear_completed_button.setCursor(Qt.PointingHandCursor)
        self.clear_completed_button.setToolTip(
            f"Remove finished/failed files from Completed to free up room "
            f"(limit is {MAX_FILES} files in progress at once)"
        )
        self.clear_completed_button.clicked.connect(self.on_clear_completed_clicked)
        bottom_row.addWidget(self.clear_completed_button)
        bottom_row.addStretch()

        self.save_all_button = QPushButton("Save All")
        self.save_all_button.setObjectName("DownloadAllButton")
        self.save_all_button.setCursor(Qt.PointingHandCursor)
        self.save_all_button.setToolTip("Saves every finished GIF next to its source WebP")
        self.save_all_button.clicked.connect(self.on_save_all_clicked)
        bottom_row.addWidget(self.save_all_button)
        layout.addLayout(bottom_row)

    def _add_slider_row(self, grid, row, name, minimum=0, maximum=100, value=0, suffix=""):
        name_label = QLabel(name)
        grid.addWidget(name_label, row, 0)

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(minimum)
        slider.setMaximum(maximum)
        slider.setValue(value)
        grid.addWidget(slider, row, 1)

        value_label = QLabel(f"{value}{suffix}")
        value_label.setMinimumWidth(70)
        grid.addWidget(value_label, row, 2)

        slider.valueChanged.connect(
            lambda v, lbl=value_label, sfx=suffix: lbl.setText(f"{v}{sfx}")
        )
        return slider, value_label

    def _scaled_preview_size(self, width, height):
        scale = min(PREVIEW_BOX_MAX_WIDTH / width, PREVIEW_BOX_MAX_HEIGHT / height)
        return QSize(max(int(width * scale), 1), max(int(height * scale), 1))

    def on_keep_original_width_toggled(self, checked):
        self.width_slider.setEnabled(not checked)
        if checked:
            self.width_label.setText("Original")
        else:
            self.width_label.setText(f"{self.width_slider.value()} px")

    # -- Shared single preview -------------------------------------------------

    def show_preview_for(self, source_path):
        """Loads one file's converted GIF into the single shared preview
        box - clicking "Show Preview" on a different row replaces it, so
        only one animated preview is ever playing at once."""
        entry = self.completed_rows.get(source_path)
        if not entry or not entry.get("output_path"):
            return

        path = entry["output_path"]
        try:
            with Image.open(path) as im:
                width, height = im.size
        except Exception:
            width, height = 200, 150

        display_size = self._scaled_preview_size(width, height)
        self.preview_label.setFixedSize(display_size)
        self.preview_label.setText("")

        movie = QMovie(path)
        movie.setScaledSize(display_size)
        self.preview_label.setMovie(movie)
        movie.start()
        self._preview_movie = movie  # keep a reference so it isn't garbage collected

        self.preview_caption.setText(f"Preview: {os.path.basename(source_path)}")
        self._previewed_path = source_path

    def _reset_preview(self):
        movie = getattr(self, "_preview_movie", None)
        if movie is not None:
            movie.stop()
        self._preview_movie = None
        self._previewed_path = None
        self.preview_label.setMovie(None)
        self.preview_label.setFixedSize(PREVIEW_PLACEHOLDER_WIDTH, PREVIEW_PLACEHOLDER_HEIGHT)
        self.preview_label.setText("Click \"Show Preview\"\non a completed file")
        self.preview_caption.setText("Nothing previewed yet")

    # -- Responsive layout -----------------------------------------------------

    # Below this width the settings column and the preview can't sit side by
    # side without squeezing, so the preview moves underneath instead.
    STACK_BELOW_WIDTH = 980

    def _apply_responsive_layout(self, stacked):
        if stacked == self._stacked_layout:
            return
        self._stacked_layout = stacked
        self.work_grid.removeWidget(self.work_left)
        self.work_grid.removeWidget(self.preview_box)
        self.work_grid.addWidget(self.work_left, 0, 0)
        if stacked:
            self.work_grid.addWidget(self.preview_box, 1, 0, Qt.AlignHCenter)
        else:
            self.work_grid.addWidget(self.preview_box, 0, 1, Qt.AlignTop)
        self.work_grid.setColumnStretch(0, 1)
        self.work_left.show()
        self.preview_box.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout(self.width() < self.STACK_BELOW_WIDTH)

    # -- File selection ------------------------------------------------------

    def choose_webps(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose WebP image(s)", "", "WebP Image (*.webp)"
        )
        if not paths:
            return
        self.add_files(paths)

    def add_files(self, paths):
        total_in_pipeline = len(self.queued_items) + len(self.completed_rows)
        room_left = MAX_FILES - total_in_pipeline
        if room_left <= 0:
            show_error(
                self, "Limit reached",
                f"You already have {MAX_FILES} files in progress. Remove "
                f"some before adding more.",
            )
            return

        if len(paths) > room_left:
            show_error(
                self, "Too many files",
                f"You selected {len(paths)} files, but only {room_left} "
                f"more can be added (limit is {MAX_FILES} total). Only the "
                f"first {room_left} will be queued.",
            )
            paths = paths[:room_left]

        for path in paths:
            if path in self.queued_items or path in self.completed_rows:
                continue  # already in the pipeline

            try:
                size_bytes = os.path.getsize(path)
                with Image.open(path) as im:
                    width, height = im.size
                    frame_count = getattr(im, "n_frames", 1)
            except Exception as exc:  # noqa: BLE001
                show_error(self, "Couldn't open WebP", f"{os.path.basename(path)}: {exc}")
                continue

            frame_word = "frame" if frame_count == 1 else "frames"
            widget = SelectedRowWidget(
                f"{os.path.basename(path)} — {width}x{height}, {frame_count} {frame_word}, "
                f"{_format_file_size(size_bytes)}",
                path,
                on_remove=lambda checked=False, p=path: self.remove_queued_file(p),
            )
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self.selected_list.addItem(item)
            self.selected_list.setItemWidget(item, widget)

            self.queued_items[path] = {"item": item, "widget": widget, "original_size": size_bytes}

        self._update_status()

    def remove_queued_file(self, path):
        entry = self.queued_items.pop(path, None)
        if entry:
            row = self.selected_list.row(entry["item"])
            self.selected_list.takeItem(row)
        self.submitted_paths.discard(path)
        self._update_status()

    # -- Starting a batch job (shared by Convert and every Target Size preset) --

    def _start_conversion_row(self, path):
        self.submitted_paths.add(path)
        entry = self.queued_items.pop(path, None)
        if entry:
            row = self.selected_list.row(entry["item"])
            self.selected_list.takeItem(row)
            original_size = entry.get("original_size", os.path.getsize(path))
        else:
            original_size = os.path.getsize(path)

        filename = os.path.basename(path)
        row_widget = WebpCompletedRowWidget(
            filename, path,
            on_save=lambda checked=False, p=path: self.save_single(p),
            on_save_to=lambda checked=False, p=path: self.save_single_to(p),
            on_preview=lambda checked=False, p=path: self.show_preview_for(p),
        )

        list_item = QListWidgetItem()
        list_item.setSizeHint(row_widget.sizeHint())
        self.completed_list.addItem(list_item)
        self.completed_list.setItemWidget(list_item, row_widget)

        self.completed_rows[path] = {
            "item": list_item, "widget": row_widget,
            "output_path": None, "output_size": None,
            "original_size": original_size,
        }
        self._update_status()

    def _finalize_row(self, path, output_path, size_bytes):
        entry = self.completed_rows.get(path)
        if not entry:
            return
        entry["output_path"] = output_path
        entry["output_size"] = size_bytes

        original_size = entry.get("original_size")
        status_text = _format_file_size(size_bytes)
        if original_size:
            reduction = 100 * (1 - size_bytes / original_size)
            change_word = "smaller" if reduction >= 0 else "larger"
            status_text += f" ({abs(reduction):.0f}% {change_word})"

        entry["widget"].mark_converted(status_text)
        self._update_status()

    def _fail_row(self, path, message):
        entry = self.completed_rows.get(path)
        if entry:
            entry["widget"].mark_failed(message)
            self.failed_paths.add(path)
        self._update_status()

    # -- Convert (manual, fixed Quality/Frames/Width for every queued file) --

    def on_convert_clicked(self):
        if not self.queued_items:
            QMessageBox.information(self, "No files", "No files have been added yet.")
            return

        quality = self.quality_slider.value()
        frames_value = self.frames_slider.value()
        width = None if self.keep_original_width_checkbox.isChecked() else self.width_slider.value()

        for path in list(self.queued_items.keys()):
            if path in self.submitted_paths:
                continue
            self._start_conversion_row(path)

            fd, output_path = tempfile.mkstemp(suffix=".gif")
            os.close(fd)

            worker = WebpToGifWorker(path, output_path, quality, frames_value, width)
            worker.signals.finished.connect(
                lambda op, sz, p=path: self._finalize_row(p, op, sz)
            )
            worker.signals.failed.connect(
                lambda msg, p=path: self._fail_row(p, msg)
            )
            self.main_window.thread_pool.start(worker)

    # -- Target size presets (search Quality per file, Frames/Width fixed) ------

    def on_custom_target_clicked(self):
        size_mib, ok = QInputDialog.getDouble(
            self, "Custom Target Size", "Target size (MiB):",
            10.0, 0.05, 2048.0, 2,
        )
        if not ok:
            return
        self.on_target_size_clicked(int(size_mib * MIB))

    def on_target_size_clicked(self, target_bytes):
        if not self.queued_items:
            QMessageBox.information(self, "No files", "No files have been added yet.")
            return

        frames_value = self.frames_slider.value()
        width = None if self.keep_original_width_checkbox.isChecked() else self.width_slider.value()

        for path in list(self.queued_items.keys()):
            if path in self.submitted_paths:
                continue
            self._start_conversion_row(path)

            fd, output_path = tempfile.mkstemp(suffix=".gif")
            os.close(fd)

            worker = WebpToGifPresetWorker(path, output_path, target_bytes, frames_value, width)
            worker.signals.finished.connect(
                lambda op, sz, q, met, p=path: self.on_target_size_finished(p, op, sz, q, met)
            )
            worker.signals.failed.connect(
                lambda msg, p=path: self._fail_row(p, msg)
            )
            self.main_window.thread_pool.start(worker)

    def on_target_size_finished(self, path, output_path, size_bytes, quality_used, met_target):
        self._finalize_row(path, output_path, size_bytes)
        if not met_target:
            show_error(
                self, "Couldn't fully meet target",
                f"{os.path.basename(path)}: even at minimum quality, the "
                f"result is {_format_file_size(size_bytes)}, which is "
                f"still over the target size. Try lowering Frames or "
                f"Width too.",
            )

    # -- Clear completed -------------------------------------------------------

    def on_clear_completed_clicked(self):
        removable_paths = [
            path for path, entry in self.completed_rows.items()
            if entry.get("output_path") is not None or path in self.failed_paths
        ]

        if not removable_paths:
            QMessageBox.information(
                self, "Nothing to clear",
                "No finished or failed files to clear yet — files still "
                "converting are left alone.",
            )
            return

        for path in removable_paths:
            row = self.completed_rows.pop(path, None)
            if row:
                list_row = self.completed_list.row(row["item"])
                self.completed_list.takeItem(list_row)
            self.failed_paths.discard(path)
            self.submitted_paths.discard(path)

        if getattr(self, "_previewed_path", None) in removable_paths:
            self._reset_preview()

        self.status_label.setText(f"Cleared {len(removable_paths)} file(s) from Completed")
        self._update_status()

    # -- Save ------------------------------------------------------------------

    def _default_output_path(self, source_path):
        base_name = os.path.splitext(os.path.basename(source_path))[0]
        return os.path.join(os.path.dirname(source_path), base_name + ".gif")

    def save_single(self, source_path):
        entry = self.completed_rows.get(source_path)
        if not entry or not entry.get("output_path"):
            return
        self._save_to(source_path, entry["output_path"], self._default_output_path(source_path))

    def save_single_to(self, source_path):
        entry = self.completed_rows.get(source_path)
        if not entry or not entry.get("output_path"):
            return
        chosen_path, _ = QFileDialog.getSaveFileName(
            self, "Save GIF As", self._default_output_path(source_path), "GIF Image (*.gif)"
        )
        if chosen_path:
            self._save_to(source_path, entry["output_path"], chosen_path)

    def _save_to(self, source_path, temp_output_path, destination):
        try:
            shutil.copy2(temp_output_path, destination)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Save failed", str(exc))
            return

        entry = self.completed_rows.get(source_path)
        if entry:
            entry["widget"].flash_saved()

        if self.main_window.delete_originals:
            deleted, delete_error = self.main_window._delete_original(source_path)
            if deleted:
                QMessageBox.information(
                    self, "Original File Deleted",
                    f"Deleted original file:\n{os.path.basename(source_path)}",
                )
            elif delete_error:
                show_error(
                    self, "Couldn't delete original",
                    f"Saved the GIF, but couldn't delete the original file:\n{delete_error}",
                )

        if self.main_window.show_filenames_after_conversion:
            self.main_window.show_filenames_window([os.path.basename(destination)])

    def on_save_all_clicked(self):
        ready_paths = [p for p, e in self.completed_rows.items() if e.get("output_path")]
        if not ready_paths:
            QMessageBox.information(self, "Nothing to save", "No files have finished converting yet.")
            return

        saved_filenames = []
        for source_path in ready_paths:
            entry = self.completed_rows[source_path]
            destination = self._default_output_path(source_path)
            try:
                shutil.copy2(entry["output_path"], destination)
            except Exception:  # noqa: BLE001
                continue

            saved_filenames.append(os.path.basename(destination))
            entry["widget"].flash_saved()

            if self.main_window.delete_originals:
                self.main_window._delete_original(source_path)

        self.status_label.setText(f"Saved {len(saved_filenames)} file(s)")

        if self.main_window.show_filenames_after_conversion:
            self.main_window.show_filenames_window(saved_filenames)

    # -- Status label ----------------------------------------------------------

    def _update_status(self):
        ready = sum(1 for p in self.queued_items if p not in self.submitted_paths)
        converting = sum(
            1 for p, e in self.completed_rows.items()
            if e.get("output_path") is None and p not in self.failed_paths
        )
        done = sum(1 for e in self.completed_rows.values() if e.get("output_path") is not None)
        failed = len(self.failed_paths)

        if not ready and not converting and not done and not failed:
            self.status_label.setText("Ready")
            return

        parts = []
        if ready:
            parts.append(f"{ready} ready to convert")
        if converting:
            parts.append(f"{converting} converting")
        if done:
            parts.append(f"{done} done")
        if failed:
            parts.append(f"{failed} failed")
        self.status_label.setText(", ".join(parts))


# ---------------------------------------------------------------------------
# Video to Image tab - pick a single frame from an MP4 and save it as PNG
# ---------------------------------------------------------------------------

class FrameExtractSignals(QObject):
    finished = pyqtSignal(object)  # PIL Image
    failed = pyqtSignal(str)


class FrameExtractWorker(QRunnable):
    def __init__(self, video_path, frame_index, fps, duration):
        super().__init__()
        self.video_path = video_path
        self.frame_index = frame_index
        self.fps = fps
        self.duration = duration
        self.signals = FrameExtractSignals()

    def run(self):
        clip = None
        try:
            clip = VideoFileClip(self.video_path)
            # Frame count is an approximation (duration * fps), so clamp
            # the requested time to just inside the clip's actual length.
            timestamp = min(self.frame_index / self.fps, max(self.duration - (1.0 / self.fps), 0))
            frame_array = clip.get_frame(timestamp)
            image = Image.fromarray(frame_array)
            self.signals.finished.emit(image)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))
        finally:
            if clip is not None:
                clip.close()


class VideoToImageTab(QWidget):
    """Handles one MP4 at a time: scrub through its frames with a slider
    (zooming in/out to narrow the range for precise picking on long
    videos), extract the exact frame you land on, and save it as a PNG."""

    MIN_ZOOM_WINDOW = 10

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.video_path = None
        self.fps = 0
        self.duration = 0.0
        self.total_frames = 0
        self.window_start = 0
        self.window_size = 0
        self.current_image = None
        self.current_frame_index = None
        self._extraction_in_progress = False
        self._pending_frame_index = None

        self._build_ui()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        if not MOVIEPY_AVAILABLE:
            warning = QLabel(
                "Video to Image needs the 'moviepy' package (and ffmpeg via "
                "'imageio-ffmpeg').\nRun: pip install moviepy imageio-ffmpeg"
            )
            warning.setWordWrap(True)
            layout.addWidget(warning)
            layout.addStretch()
            return

        open_button = QPushButton("Open Video…")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.clicked.connect(self.choose_video)
        layout.addWidget(open_button)

        self.file_label = QLabel("No video selected")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        preview_row = QHBoxLayout()
        self.preview_label = QLabel("No frame extracted yet")
        self.preview_label.setFixedSize(PREVIEW_PLACEHOLDER_WIDTH, PREVIEW_PLACEHOLDER_HEIGHT)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setObjectName("PreviewBox")
        preview_row.addWidget(self.preview_label)

        info_col = QVBoxLayout()
        self.frame_info_label = QLabel("")
        self.frame_info_label.setStyleSheet("font-weight: 700;")
        info_col.addWidget(self.frame_info_label)
        info_col.addStretch()
        preview_row.addLayout(info_col, stretch=1)
        layout.addLayout(preview_row)

        slider_box = QGroupBox("Frame")
        slider_layout = QVBoxLayout(slider_box)

        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.setEnabled(False)
        self.frame_slider.valueChanged.connect(self.on_slider_moved)
        slider_layout.addWidget(self.frame_slider)

        zoom_row = QHBoxLayout()
        self.zoom_label = QLabel("")
        zoom_row.addWidget(self.zoom_label, stretch=1)

        self.zoom_out_button = QPushButton("Zoom Out")
        self.zoom_out_button.setCursor(Qt.PointingHandCursor)
        self.zoom_out_button.setEnabled(False)
        self.zoom_out_button.clicked.connect(self.on_zoom_out)
        zoom_row.addWidget(self.zoom_out_button)

        self.zoom_in_button = QPushButton("Zoom In")
        self.zoom_in_button.setCursor(Qt.PointingHandCursor)
        self.zoom_in_button.setEnabled(False)
        self.zoom_in_button.clicked.connect(self.on_zoom_in)
        zoom_row.addWidget(self.zoom_in_button)

        slider_layout.addLayout(zoom_row)
        layout.addWidget(slider_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("NiceProgressBar")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        buttons_row = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("DownloadAllButton")
        self.save_button.setToolTip("Saves to same location file was found")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save_clicked)
        buttons_row.addWidget(self.save_button)

        self.save_to_button = QPushButton("Save To")
        self.save_to_button.setToolTip("Choose a location and file name to save to")
        self.save_to_button.setCursor(Qt.PointingHandCursor)
        self.save_to_button.setEnabled(False)
        self.save_to_button.clicked.connect(self.on_save_to_clicked)
        buttons_row.addWidget(self.save_to_button)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        layout.addStretch()

    # -- Video selection ------------------------------------------------

    def choose_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a video", "", "MP4 Video (*.mp4)"
        )
        if not path:
            return

        try:
            clip = VideoFileClip(path)
            duration = clip.duration
            fps = clip.fps or 24
            clip.close()
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Couldn't open video", str(exc))
            return

        self.video_path = path
        self.duration = duration
        self.fps = fps
        # Frame count is an estimate - not every video has a perfectly
        # constant frame rate, so this is "close enough" for scrubbing.
        self.total_frames = max(int(duration * fps) - 1, 1)
        self.window_start = 0
        self.window_size = self.total_frames + 1

        self.file_label.setText(
            f"{os.path.basename(path)} — {duration:.1f}s, ~{self.total_frames + 1} frames at {fps:.1f} fps"
        )

        self.frame_slider.blockSignals(True)
        self.frame_slider.setEnabled(True)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(self.total_frames)
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)

        self._update_zoom_label()
        self._update_frame_info_label(0)
        self._update_zoom_buttons()

        self.current_image = None
        self.current_frame_index = None
        self._pending_frame_index = None
        self.preview_label.clear()
        self.preview_label.setText("Loading first frame…")
        self.preview_label.setFixedSize(PREVIEW_PLACEHOLDER_WIDTH, PREVIEW_PLACEHOLDER_HEIGHT)
        self.save_button.setEnabled(False)
        self.save_to_button.setEnabled(False)

        self._request_extraction(0)

    # -- Slider / zoom ------------------------------------------------

    def on_slider_moved(self, value):
        self._update_frame_info_label(value)
        self._request_extraction(value)

    def _update_frame_info_label(self, frame_index):
        timestamp = frame_index / self.fps if self.fps else 0
        self.frame_info_label.setText(
            f"Frame {frame_index} of {self.total_frames} — {timestamp:.2f}s"
        )

    def _update_zoom_label(self):
        window_end = self.window_start + self.window_size - 1
        self.zoom_label.setText(
            f"Showing frames {self.window_start}-{window_end} of {self.total_frames}"
        )

    def _update_zoom_buttons(self):
        self.zoom_in_button.setEnabled(self.window_size > self.MIN_ZOOM_WINDOW)
        self.zoom_out_button.setEnabled(self.window_size <= self.total_frames)

    def _apply_zoom(self, new_window_size):
        new_window_size = max(self.MIN_ZOOM_WINDOW, min(new_window_size, self.total_frames + 1))
        current_value = self.frame_slider.value()

        new_start = current_value - new_window_size // 2
        new_start = max(0, min(new_start, self.total_frames + 1 - new_window_size))
        new_end = new_start + new_window_size - 1

        self.window_start = new_start
        self.window_size = new_window_size

        self.frame_slider.blockSignals(True)
        self.frame_slider.setMinimum(new_start)
        self.frame_slider.setMaximum(new_end)
        self.frame_slider.setValue(current_value)
        self.frame_slider.blockSignals(False)

        self._update_zoom_label()
        self._update_zoom_buttons()

    def on_zoom_in(self):
        self._apply_zoom(self.window_size // 2)

    def on_zoom_out(self):
        self._apply_zoom(self.window_size * 2)

    # -- Extract ------------------------------------------------------------

    def _request_extraction(self, frame_index):
        if not self.video_path:
            return
        if self._extraction_in_progress:
            # Don't pile up a job per pixel of drag - just remember the
            # latest frame requested and jump straight to it once the
            # in-flight extraction finishes.
            self._pending_frame_index = frame_index
            return
        self._start_extraction(frame_index)

    def _start_extraction(self, frame_index):
        self._extraction_in_progress = True
        self.progress_bar.show()

        worker = FrameExtractWorker(self.video_path, frame_index, self.fps, self.duration)
        worker.signals.finished.connect(lambda image: self.on_extract_finished(image, frame_index))
        worker.signals.failed.connect(self.on_extract_failed)
        self.main_window.thread_pool.start(worker)

    def on_extract_finished(self, image, frame_index):
        self._extraction_in_progress = False
        self.progress_bar.hide()
        self.current_image = image
        self.current_frame_index = frame_index
        self._show_preview(image)
        self.save_button.setEnabled(True)
        self.save_to_button.setEnabled(True)
        self._extract_next_pending()

    def on_extract_failed(self, error_message):
        self._extraction_in_progress = False
        self.progress_bar.hide()
        show_error(self, "Couldn't extract frame", error_message)
        self._extract_next_pending()

    def _extract_next_pending(self):
        if self._pending_frame_index is not None:
            next_index = self._pending_frame_index
            self._pending_frame_index = None
            self._start_extraction(next_index)

    def _scaled_preview_size(self, width, height):
        scale = min(PREVIEW_BOX_MAX_WIDTH / width, PREVIEW_BOX_MAX_HEIGHT / height)
        return QSize(max(int(width * scale), 1), max(int(height * scale), 1))

    def _show_preview(self, pil_image):
        display_size = self._scaled_preview_size(*pil_image.size)
        self.preview_label.setFixedSize(display_size)

        fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            pil_image.save(temp_path, "PNG")
            pixmap = QPixmap(temp_path)
            self.preview_label.setPixmap(
                pixmap.scaled(display_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    # -- Save ----------------------------------------------------------------

    def _default_frame_path(self):
        base_name = os.path.splitext(os.path.basename(self.video_path))[0]
        return os.path.join(
            os.path.dirname(self.video_path),
            f"{base_name}-frame{self.current_frame_index}.png",
        )

    def on_save_clicked(self):
        self._save_to(self._default_frame_path())

    def on_save_to_clicked(self):
        chosen_path, _ = QFileDialog.getSaveFileName(
            self, "Save Frame As", self._default_frame_path(), "PNG Image (*.png)"
        )
        if chosen_path:
            self._save_to(chosen_path)

    def _save_to(self, destination):
        if self.current_image is None:
            return
        try:
            self.current_image.save(destination, "PNG")
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Save failed", str(exc))
            return

        QMessageBox.information(self, "Saved", f"Saved {os.path.basename(destination)}")


# ---------------------------------------------------------------------------
# Update checker - looks at GitHub Releases in a background thread
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Web Images tab
#
# Merged in from the standalone "Web Image Browser" app. That one was written
# against PySide6; everything here is ported to PyQt6 so it shares this app's
# event loop, thread pool and light/dark theme instead of running separately.
#
# The tab has its own secondary nav bar with two pages:
#   Browser        - embedded Chromium, collect every image a page renders
#   Overlay Studio - composite one image over another (animation preserved)
# ---------------------------------------------------------------------------

WEB_CARD_WIDTH = 250
WEB_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # skip anything over 50 MB
WEB_MAX_LAYER_FRAMES = 400
WEB_MAX_OUTPUT_FRAMES = 600

# Colours for the few things drawn in code rather than styled by the app-wide
# stylesheet: the Overlay Studio's canvas background, transparency checker and
# canvas outline. Everything else in this tab uses named styles from
# _shared_style(), so it follows Light/Dark automatically.
WEB_THEME = {
    "light": {
        "BG": "#f5f5f7", "BORDER": "#d9d9dc", "MUTED": "#6e6e73",
        "CHECK_A": "#ececef", "CHECK_B": "#f8f8fa",
    },
    "dark": {
        "BG": "#1e1e1e", "BORDER": "#3a3a3c", "MUTED": "#a1a1a6",
        "CHECK_A": "#2c2c2e", "CHECK_B": "#242426",
    },
}
WEB_COLORS = dict(WEB_THEME["light"])

WEB_IMAGE_FILTER = (
    "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.tif *.tiff *.ico *.svg *.avif);;"
    "All files (*)"
)


def set_web_theme(theme_name):
    WEB_COLORS.update(WEB_THEME.get(theme_name, WEB_THEME["light"]))


def qt_alive(obj):
    """True unless Qt has already deleted the C++ side of this widget."""
    try:
        return not sip.isdeleted(obj)
    except (TypeError, RuntimeError):
        return True


def web_output_formats():
    formats = ["PNG", "GIF", "WebP"]
    if AVIF_AVAILABLE:
        formats.append("AVIF")
    return formats


# -- Thread -> UI bridge ----------------------------------------------------

class UiBridge(QObject):
    """Qt widgets may only be touched from the GUI thread. Worker threads emit
    a callable through this queued signal and it is executed on the GUI thread."""

    call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @staticmethod
    def _run(fn):
        try:
            fn()
        except RuntimeError:
            # Target widget was deleted (e.g. gallery cleared by a new scan).
            pass


# -- Downloading / decoding -------------------------------------------------

def _qbytearray_to_str(value):
    try:
        return bytes(value.data()).decode("utf-8", "ignore")
    except AttributeError:
        return str(value)


class WebFetcher:
    """Shared HTTP session that mirrors the embedded browser's user agent,
    cookies and referer so hot-link-protected images still download. Uses
    requests when it's installed and falls back to urllib when it isn't."""

    def __init__(self):
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        self.user_agent = ""
        self.referer = ""
        self._cookies = {}  # (domain, name) -> value, for the urllib fallback
        self._cache = {}
        self._lock = threading.Lock()

    def set_user_agent(self, user_agent):
        self.user_agent = user_agent
        if self.session is not None:
            self.session.headers["User-Agent"] = user_agent

    def add_cookie(self, cookie):
        try:
            name = _qbytearray_to_str(cookie.name())
            value = _qbytearray_to_str(cookie.value())
            domain = cookie.domain()
            path = cookie.path() or "/"
        except Exception:  # noqa: BLE001
            return
        if self.session is not None:
            try:
                self.session.cookies.set(name, value, domain=domain, path=path)
            except Exception:  # noqa: BLE001
                pass
        else:
            self._cookies[(domain, name)] = value

    def clear_cache(self):
        with self._lock:
            self._cache.clear()

    def _headers(self):
        headers = {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        if self.referer:
            headers["Referer"] = self.referer
        return headers

    def get(self, url):
        with self._lock:
            if url in self._cache:
                return self._cache[url]

        if url.startswith("data:"):
            data = decode_data_uri(url)
        elif self.session is not None:
            with self.session.get(url, headers=self._headers(),
                                  timeout=20, stream=True) as response:
                response.raise_for_status()
                buffer = io.BytesIO()
                for chunk in response.iter_content(65536):
                    buffer.write(chunk)
                    if buffer.tell() > WEB_MAX_DOWNLOAD_BYTES:
                        raise ValueError("Image is larger than 50 MB")
                data = buffer.getvalue()
        else:
            data = self._urllib_get(url)

        with self._lock:
            self._cache[url] = data
        return data

    def _urllib_get(self, url):
        headers = self._headers()
        host = (urlparse(url).hostname or "").lower()
        jar = []
        for (domain, name), value in self._cookies.items():
            domain = (domain or "").lower().lstrip(".")
            if domain and (host == domain or host.endswith("." + domain)):
                jar.append(f"{name}={value}")
        if jar:
            headers["Cookie"] = "; ".join(jar)

        request = urllib.request.Request(url, headers=headers)
        buffer = io.BytesIO()
        with urllib.request.urlopen(request, timeout=20) as response:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                buffer.write(chunk)
                if buffer.tell() > WEB_MAX_DOWNLOAD_BYTES:
                    raise ValueError("Image is larger than 50 MB")
        return buffer.getvalue()


def decode_data_uri(uri):
    header, _, payload = uri.partition(",")
    if header.endswith(";base64"):
        return base64.b64decode(payload)
    return unquote_to_bytes(payload)


def web_open_image(data):
    """Open bytes with Pillow; fall back to Qt's decoders (SVG, ICO variants,
    etc.) and hand the result back to Pillow as PNG."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception:  # noqa: BLE001
        pass

    qimage = QImage.fromData(QByteArray(data))
    if qimage.isNull():
        raise ValueError("Unsupported or corrupt image format")
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QIODevice.WriteOnly)
    qimage.save(buffer, "PNG")
    buffer.close()
    img = Image.open(io.BytesIO(bytes(array.data())))
    img.load()
    return img


def web_to_rgba(img):
    """Convert any Pillow mode (including 16-bit / float) to RGBA."""
    if img.mode == "RGBA":
        return img.copy()
    try:
        return img.convert("RGBA")
    except (ValueError, OSError):
        # 16/32-bit integer or float greyscale can't go straight to RGBA.
        return img.convert("I").point(lambda v: v / 256).convert("L").convert("RGBA")


def web_frame_count(img):
    return getattr(img, "n_frames", 1) if getattr(img, "is_animated", False) else 1


def _web_save_still(img, path, fmt):
    if fmt == "png":
        img.save(path, "PNG", optimize=True)
    elif fmt == "webp":
        img.save(path, "WEBP", quality=95, method=6)
    elif fmt == "avif":
        img.save(path, "AVIF", quality=90)
    else:
        img.save(path, "GIF")


def _web_save_animation(frames, durations, path, fmt, loop=0):
    if fmt == "webp":
        frames[0].save(path, "WEBP", save_all=True, append_images=frames[1:],
                       duration=durations, loop=loop, quality=95, method=6)
    elif fmt == "avif":
        # Not every AVIF plugin can write multi-frame files; fall back to a
        # still of the first frame rather than failing the whole save.
        try:
            frames[0].save(path, "AVIF", save_all=True, append_images=frames[1:],
                           duration=durations, loop=loop, quality=90)
        except Exception:  # noqa: BLE001
            _web_save_still(frames[0], path, "avif")
    else:
        frames[0].save(path, "GIF", save_all=True, append_images=frames[1:],
                       duration=durations, loop=loop, disposal=2, optimize=False)


def web_save_image(img, path, fmt):
    animated = web_frame_count(img) > 1
    loop = img.info.get("loop", 0)

    if fmt == "png" or not animated:
        if animated:
            img.seek(0)
        _web_save_still(web_to_rgba(img), path, fmt)
        return

    frames, durations = [], []
    for frame in ImageSequence.Iterator(img):
        frames.append(web_to_rgba(frame))
        durations.append(int(frame.info.get("duration", 100)) or 100)
    _web_save_animation(frames, durations, path, fmt, loop)


# -- Overlay compositing (pure Pillow - also used for export) ---------------

def pil_to_qimage(img):
    img = img if img.mode == "RGBA" else web_to_rgba(img)
    return QImage(
        img.tobytes("raw", "RGBA"), img.width, img.height,
        QImage.Format_RGBA8888,
    ).copy()


class Layer:
    """An imported image (still or animated) broken into RGBA frames."""

    def __init__(self, name, frames, durations):
        self.name = name
        self.frames = frames
        self.durations = durations
        self.cum = []
        total = 0
        for duration in durations:
            total += duration
            self.cum.append(total)
        self.qimages = []
        self.pixmaps = []  # created on the GUI thread

    @property
    def size(self):
        return self.frames[0].size

    @property
    def animated(self):
        return len(self.frames) > 1

    @property
    def total(self):
        return self.cum[-1]

    def index_at(self, t_ms):
        if not self.animated:
            return 0
        return min(bisect.bisect_right(self.cum, t_ms % self.total), len(self.frames) - 1)

    def boundaries(self, until):
        """Frame start times, repeating the animation until `until` ms."""
        out = []
        base = 0
        while base < until:
            out.append(base)
            for c in self.cum[:-1]:
                if base + c < until:
                    out.append(base + c)
            base += self.total
        return out


def build_layer(data, name):
    img = web_open_image(data)
    frames, durations = [], []
    if web_frame_count(img) > 1:
        for i, frame in enumerate(ImageSequence.Iterator(img)):
            if i >= WEB_MAX_LAYER_FRAMES:
                break
            frames.append(web_to_rgba(frame))
            duration = int(frame.info.get("duration", 100) or 100)
            durations.append(max(duration, 20))
    else:
        frames = [web_to_rgba(img)]
        durations = [100]
    layer = Layer(name, frames, durations)
    layer.qimages = [pil_to_qimage(f) for f in frames]
    return layer


def compose_frames(bg, fg, x, y, scale, opacity, animate):
    """Return (frames, durations) of fg placed on bg at (x, y) in bg pixels."""
    if animate and (bg.animated or (fg and fg.animated)):
        total = max(bg.total, fg.total if fg else 0)
        starts = set(bg.boundaries(total))
        if fg:
            starts.update(fg.boundaries(total))
        starts = sorted(starts)[:WEB_MAX_OUTPUT_FRAMES]
        ends = starts[1:] + [total]
        timeline = []
        for start, end in zip(starts, ends):
            if end - start < 10 and timeline:  # merge slivers into previous frame
                timeline[-1] = (timeline[-1][0], timeline[-1][1] + (end - start))
                continue
            timeline.append((start, end - start))
    else:
        timeline = [(0, 100)]

    scaled_cache = {}

    def fg_frame(index):
        if index not in scaled_cache:
            frame = fg.frames[index]
            width = max(1, round(frame.width * scale))
            height = max(1, round(frame.height * scale))
            if (width, height) != frame.size:
                frame = frame.resize((width, height), RESAMPLE_LANCZOS)
            if opacity < 1.0:
                frame = frame.copy()
                alpha = frame.getchannel("A").point(lambda v: round(v * opacity))
                frame.putalpha(alpha)
            scaled_cache[index] = frame
        return scaled_cache[index]

    frames, durations = [], []
    for start, duration in timeline:
        base = bg.frames[bg.index_at(start)].copy()
        if fg:
            layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
            layer.paste(fg_frame(fg.index_at(start)), (round(x), round(y)))  # clips off-canvas
            base = Image.alpha_composite(base, layer)
        frames.append(base)
        durations.append(max(20, int(duration)))
    return frames, durations


def save_frames(frames, durations, path, fmt):
    if fmt == "png" or len(frames) == 1:
        _web_save_still(frames[0], path, fmt)
        return
    _web_save_animation(frames, durations, path, fmt, loop=0)


# -- Shared helpers ---------------------------------------------------------

def short_error(exc):
    text = str(exc) or exc.__class__.__name__
    return text if len(text) < 120 else text[:117] + "…"


def checker_brush():
    pixmap = QPixmap(20, 20)
    pixmap.fill(QColor(WEB_COLORS["CHECK_A"]))
    painter = QPainter(pixmap)
    painter.fillRect(0, 0, 10, 10, QColor(WEB_COLORS["CHECK_B"]))
    painter.fillRect(10, 10, 10, 10, QColor(WEB_COLORS["CHECK_B"]))
    painter.end()
    return QBrush(pixmap)


def _muted(text, hint=False):
    """A secondary-text label styled by the app-wide sheet."""
    label = QLabel(text)
    label.setObjectName("HintLabel" if hint else "MutedLabel")
    label.setWordWrap(True)
    return label


def _primary_button(text):
    button = QPushButton(text)
    button.setObjectName("ConvertButton")
    button.setCursor(Qt.PointingHandCursor)
    return button


def _plain_button(text, name=None):
    button = QPushButton(text)
    if name:
        button.setObjectName(name)
    button.setCursor(Qt.PointingHandCursor)
    return button


# -- Browser page -----------------------------------------------------------

# Qt 6's WebEngine is a recent Chromium, so these stand-ins are normally
# redundant - each one only installs itself if the method is genuinely
# missing. They're kept so the app still behaves on an older Qt build, where
# a site calling a newer method would throw, stop being interactive, and look
# like its buttons simply don't respond.
COMPAT_POLYFILL_JS = r"""
(function () {
  try {
    var def = function (target, name, value) {
      if (target && !target[name]) {
        Object.defineProperty(target, name, {
          value: value, writable: true, configurable: true, enumerable: false
        });
      }
    };
    var relIndex = function (length, index) {
      index = Math.trunc(index) || 0;
      if (index < 0) index += length;
      return (index < 0 || index >= length) ? -1 : index;
    };

    def(Array.prototype, 'at', function (index) {
      var i = relIndex(this.length, index);
      return i < 0 ? undefined : this[i];
    });
    def(String.prototype, 'at', function (index) {
      var i = relIndex(this.length, index);
      return i < 0 ? undefined : this[i];
    });
    def(Object, 'hasOwn', function (object, key) {
      return Object.prototype.hasOwnProperty.call(Object(object), key);
    });
    def(Array.prototype, 'findLast', function (fn, thisArg) {
      for (var i = this.length - 1; i >= 0; i--) {
        if (fn.call(thisArg, this[i], i, this)) return this[i];
      }
      return undefined;
    });
    def(Array.prototype, 'findLastIndex', function (fn, thisArg) {
      for (var i = this.length - 1; i >= 0; i--) {
        if (fn.call(thisArg, this[i], i, this)) return i;
      }
      return -1;
    });
    def(Array.prototype, 'toSorted', function (fn) {
      return Array.prototype.slice.call(this).sort(fn);
    });
    def(Array.prototype, 'toReversed', function () {
      return Array.prototype.slice.call(this).reverse();
    });
    def(Array.prototype, 'with', function (index, value) {
      var copy = Array.prototype.slice.call(this);
      var i = relIndex(copy.length, index);
      if (i >= 0) copy[i] = value;
      return copy;
    });
    def(Object, 'groupBy', function (items, fn) {
      var out = Object.create(null), i = 0;
      Array.from(items).forEach(function (item) {
        var key = fn(item, i++);
        (out[key] = out[key] || []).push(item);
      });
      return out;
    });
    def(String.prototype, 'replaceAll', function (search, replacement) {
      if (Object.prototype.toString.call(search) === '[object RegExp]') {
        return this.replace(search, replacement);
      }
      return this.split(search).join(replacement);
    });
    if (typeof window.structuredClone !== 'function') {
      window.structuredClone = function (value) {
        try { return JSON.parse(JSON.stringify(value)); } catch (e) { return value; }
      };
    }
  } catch (e) {
    /* never let the shim itself break a page */
  }
})();
"""


def install_compat_polyfills(profile):
    """Runs COMPAT_POLYFILL_JS at the start of every page, in every frame."""
    if QWebEngineScript is None:
        return
    name = "imagegen_compat_shim"
    if profile.scripts().find(name):
        return  # already installed on this profile
    script = QWebEngineScript()
    script.setName(name)
    script.setSourceCode(COMPAT_POLYFILL_JS)
    script.setInjectionPoint(QWebEngineScript.DocumentCreation)
    script.setWorldId(QWebEngineScript.MainWorld)
    script.setRunsOnSubFrames(True)
    profile.scripts().insert(script)


if WEBENGINE_AVAILABLE:

    class LoggingWebPage(QWebEnginePage):
        """A page that remembers the JavaScript errors a site reports, so a
        site that silently refuses to work can be diagnosed."""

        def __init__(self, profile, parent, on_error):
            super().__init__(profile, parent)
            self._on_error = on_error

        def javaScriptConsoleMessage(self, level, message, line, source):
            if level == QWebEnginePage.ErrorMessageLevel:
                where = os.path.basename(source or "") or "page"
                self._on_error(f"{where}:{line}  {message}")

if WEBENGINE_AVAILABLE:

    class BrowserView(QWebEngineView):
        """One browser tab's web view."""

        def __init__(self, owner):
            super().__init__()
            self.owner = owner

        def createWindow(self, window_type):
            # Anything the page tries to open in a new window or tab
            # (target="_blank", window.open(), ctrl/middle-click) gets a real
            # tab here instead of replacing the page the user is on.
            background = window_type == QWebEnginePage.WebBrowserBackgroundTab
            return self.owner.new_tab(background=background)


class WebImageCard(QFrame):
    """One collected image: preview, details, format picker and Save."""

    def __init__(self, url, index, page):
        super().__init__()
        self.url = url
        self.index = index
        self.page = page
        self.tab = page.tab
        self.scan_id = page.scan_id

        self.setObjectName("ImageCard")
        self.setFixedWidth(WEB_CARD_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.preview = QLabel("Loading preview…")
        self.preview.setObjectName("PreviewBox")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(160)
        layout.addWidget(self.preview)

        self.info = QLabel(f"Image {index + 1}")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.info)

        shown = url if not url.startswith("data:") else url[:40] + "… (embedded)"
        self.url_label = _muted(shown, hint=True)
        self.url_label.setMaximumHeight(34)
        self.url_label.setToolTip(url if len(url) < 2000 else shown)
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.url_label)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.format_box = QComboBox()
        self.format_box.addItems(web_output_formats())
        row.addWidget(self.format_box, 1)

        self.save_btn = _primary_button("Save")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn)

        more = QToolButton()
        more.setText("⋯")
        more.setToolTip("Send to Overlay Studio")
        more.setCursor(Qt.PointingHandCursor)
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        background_action = QAction("Use as Overlay background", menu)
        forefront_action = QAction("Use as Overlay forefront", menu)
        background_action.triggered.connect(lambda: self.send_to_overlay("bg"))
        forefront_action.triggered.connect(lambda: self.send_to_overlay("fg"))
        menu.addAction(background_action)
        menu.addAction(forefront_action)
        more.setMenu(menu)
        row.addWidget(more)
        layout.addLayout(row)

        self.tab.pool.submit(self._load_preview)

    def _alive(self):
        return qt_alive(self) and self.scan_id == self.page.scan_id

    def _ui(self, fn):
        def guarded():
            if self._alive():
                fn()
        self.tab.bridge.call.emit(guarded)

    def _load_preview(self):
        if not self._alive():
            return
        ok = False
        try:
            data = self.tab.fetcher.get(self.url)
            img = web_open_image(data)
            width, height = img.size
            fmt = (img.format or "image").upper()
            frames = web_frame_count(img)
            if frames > 1:
                img.seek(0)
            thumb = web_to_rgba(img)
            thumb.thumbnail((WEB_CARD_WIDTH - 30, 150), RESAMPLE_LANCZOS)
            qimage = pil_to_qimage(thumb)

            details = f"{width} × {height}  •  {fmt}"
            if frames > 1:
                details += f"  •  {frames} frames"

            def update():
                self.preview.setPixmap(QPixmap.fromImage(qimage))
                self.info.setText(f"Image {self.index + 1}  •  {details}")
            self._ui(update)
            ok = True
        except Exception as exc:  # noqa: BLE001
            message = f"Preview unavailable\n{short_error(exc)}"
            self._ui(lambda: self.preview.setText(message))
        scan = self.scan_id
        self.tab.bridge.call.emit(lambda: self.page.preview_done(scan, ok))

    def _suggested_name(self):
        base = os.path.splitext(os.path.basename(urlparse(self.url).path))[0]
        if not base or self.url.startswith("data:"):
            base = f"image_{self.index + 1}"
        return base

    def send_to_overlay(self, role):
        url, name = self.url, self._suggested_name()
        self.tab.overlay_page.load_async(role, lambda: self.tab.fetcher.get(url), name)
        where = "background" if role == "bg" else "forefront"
        self.page.set_status(f"Sent image {self.index + 1} to Overlay Studio as {where}.")
        self.tab.show_page(1)

    def save(self):
        fmt = self.format_box.currentText().lower()
        suffix = "." + fmt
        default = os.path.join(self.tab.last_save_dir, self._suggested_name() + suffix)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Image", default, f"{fmt.upper()} Image (*{suffix})"
        )
        if not path:
            return
        if not path.lower().endswith(suffix):
            path += suffix
        self.tab.last_save_dir = os.path.dirname(path)

        self.save_btn.setEnabled(False)
        self.save_btn.setText("…")
        threading.Thread(target=self._save_worker, args=(path, fmt), daemon=True).start()

    def _save_worker(self, path, fmt):
        error = None
        try:
            img = web_open_image(self.tab.fetcher.get(self.url))
            web_save_image(img, path, fmt)
            name = os.path.basename(path)
            self.tab.bridge.call.emit(lambda: self.page.set_status(f"Saved {name}"))
        except Exception as exc:  # noqa: BLE001
            error = short_error(exc)

        def done():
            self.save_btn.setEnabled(True)
            self.save_btn.setText("Save")
            if error:
                show_critical(self, "Save failed", error)
        self._ui(done)


COLLECT_JS = r"""
(() => {
    const out = new Set();
    const add = (u) => {
        if (!u || typeof u !== 'string') return;
        u = u.trim();
        if (!u || u.startsWith('blob:') || u.startsWith('javascript:')) return;
        if (u.startsWith('data:')) {
            // keep real embedded images, skip tiny placeholder pixels
            if (u.startsWith('data:image/') && u.length > 400) out.add(u);
            return;
        }
        try {
            const abs = new URL(u, location.href);
            if (abs.protocol === 'http:' || abs.protocol === 'https:') out.add(abs.href);
        } catch (e) {}
    };
    const addSrcset = (ss) => {
        if (ss) ss.split(/,\s+/).forEach(x => add(x.trim().split(/\s+/)[0]));
    };

    document.querySelectorAll('img').forEach(img => {
        add(img.currentSrc);
        add(img.src);
        ['data-src', 'data-original', 'data-lazy-src', 'data-image', 'data-url', 'data-full']
            .forEach(a => add(img.getAttribute(a)));
        addSrcset(img.getAttribute('srcset'));
        addSrcset(img.getAttribute('data-srcset'));
    });

    document.querySelectorAll('picture source, source[type^="image"]').forEach(s => {
        addSrcset(s.getAttribute('srcset'));
        addSrcset(s.getAttribute('data-srcset'));
    });

    document.querySelectorAll('svg image').forEach(i => {
        add(i.getAttribute('href') || i.getAttribute('xlink:href'));
    });

    document.querySelectorAll('video[poster]').forEach(v => add(v.poster));

    const urlRe = /url\(\s*(['"]?)(.*?)\1\s*\)/g;
    const scanBg = (value) => {
        if (value && value !== 'none') {
            for (const m of value.matchAll(urlRe)) add(m[2]);
        }
    };
    document.querySelectorAll('*').forEach(el => {
        const cs = getComputedStyle(el);
        scanBg(cs.backgroundImage);
        scanBg(getComputedStyle(el, '::before').backgroundImage);
        scanBg(getComputedStyle(el, '::after').backgroundImage);
    });

    document.querySelectorAll(
        'meta[property="og:image"], meta[property="og:image:url"], ' +
        'meta[name="twitter:image"], meta[name="twitter:image:src"]'
    ).forEach(m => add(m.content));

    document.querySelectorAll('a[href]').forEach(a => {
        if (/\.(png|jpe?g|gif|webp|avif|bmp|svg)(\?|#|$)/i.test(a.getAttribute('href') || ''))
            add(a.href);
    });

    return JSON.stringify([...out]);
})()
"""

# Scrolls through the page so lazy-loaded images get requested, then returns.
SCROLL_JS = r"""
(() => {
    window.__wibScrollDone = false;
    const step = Math.max(300, window.innerHeight * 0.8);
    let y = 0;
    const tick = () => {
        const max = document.documentElement.scrollHeight;
        y += step;
        window.scrollTo(0, y);
        if (y < max && y < 60000) setTimeout(tick, 120);
        else setTimeout(() => { window.scrollTo(0, 0); window.__wibScrollDone = true; }, 400);
    };
    tick();
    return true;
})()
"""


class GripSplitterHandle(QSplitterHandle):
    """A slim, centred grip instead of a full-width bar - obviously draggable
    without looking heavy. Highlights in the accent colour on hover."""

    def __init__(self, orientation, parent, draw_grip=True):
        super().__init__(orientation, parent)
        # A QSplitter also creates a handle before its first widget, which is
        # never draggable - that one gets no grip drawn.
        self.draw_grip = draw_grip
        self._hover = False
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip("Drag to resize")

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if not self.draw_grip:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#7a5cff" if self._hover else WEB_COLORS["BORDER"]))
        grip_width, grip_height = 56, 4
        painter.drawRoundedRect(
            QRectF((self.width() - grip_width) / 2, (self.height() - grip_height) / 2,
                   grip_width, grip_height),
            2, 2,
        )
        painter.end()


class GripSplitter(QSplitter):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._handles_made = 0

    def createHandle(self):
        self._handles_made += 1
        return GripSplitterHandle(
            self.orientation(), self, draw_grip=self._handles_made > 1
        )


class DetachedBrowserWindow(QWidget):
    """The embedded browser, popped out into an ordinary window of its own.

    The main app window is frameless with a custom title bar, and the browser
    sits several layers deep inside it. If anything about that embedding stops
    the page receiving mouse clicks on a particular machine, this button gives
    a plain, normally-framed window to browse in instead. Collecting images
    still works exactly the same - the gallery stays in the main window."""

    def __init__(self, owner, pane):
        super().__init__()
        self.owner = owner
        self.setWindowTitle("Web Images - browser")
        self.setWindowIcon(app_icon())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(pane)
        # Taking the pane out of a QStackedWidget hides it, and moving it into
        # this layout doesn't undo that - so show it explicitly.
        pane.show()
        self.resize(1100, 820)

    def closeEvent(self, event):
        # owner is cleared when the main window is putting the browser back
        # itself, so this only fires when the user closes this window.
        if self.owner is not None:
            self.owner.reattach_browser()
        event.accept()


class WebBrowserPage(QWidget):
    """Embedded Chromium on top, the collected-image gallery underneath, with a
    draggable splitter between them. The browser gets most of the height by
    default; drag the bar down to give it even more, or up to see more cards."""

    DEFAULT_TITLE = (
        "Browse a webpage like a normal browser, then collect the images that "
        "actually appear on it."
    )

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.scan_id = 0
        self.preview_pending = 0
        self.preview_failed = 0
        self.images = []
        self._scroll_polls = 0
        self._sized_splitter = False
        # Defined up front so shutdown() and friends are safe even when Qt
        # WebEngine is missing and none of the browser is built.
        self.detached_window = None
        self.browser_pane = None
        self.page_errors = []

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(12)

        self.page_title = _muted(self.DEFAULT_TITLE)

        if not WEBENGINE_AVAILABLE:
            main.addWidget(self.page_title)
            self._build_unavailable(main)
            return

        # Navigation bar
        nav = QHBoxLayout()
        nav.setSpacing(8)
        back = _plain_button("‹", "IconButton")
        forward = _plain_button("›", "IconButton")
        reload_button = _plain_button("↻", "IconButton")
        for button, tip in ((back, "Back"), (forward, "Forward"), (reload_button, "Reload")):
            button.setFixedSize(38, 38)
            button.setToolTip(tip)
            nav.addWidget(button)

        self.url = QLineEdit()
        self.url.setObjectName("UrlBar")
        self.url.setPlaceholderText("Enter a webpage URL, or type words to search…")
        self.url.setMinimumHeight(38)
        nav.addWidget(self.url, 1)

        go = _primary_button("Go")
        go.setMinimumHeight(38)
        go.setMinimumWidth(70)
        nav.addWidget(go)

        new_tab_button = _plain_button("+", "IconButton")
        new_tab_button.setFixedSize(38, 38)
        new_tab_button.setToolTip("New tab (Ctrl+T)")
        new_tab_button.clicked.connect(lambda: self.new_tab(focus_url=True))
        nav.addWidget(new_tab_button)

        self.detach_button = _plain_button("⧉", "IconButton")
        self.detach_button.setFixedSize(38, 38)
        self.detach_button.setToolTip(
            "Open the browser in its own window.\n"
            "Use this if clicking inside the page doesn't work here."
        )
        self.detach_button.clicked.connect(self.toggle_detached)
        nav.addWidget(self.detach_button)

        # Browser tabs. All tabs share the default profile, so they share
        # cookies and logins with each other and with the image downloader.
        self.profile = QWebEngineProfile.defaultProfile()
        self.tab.fetcher.set_user_agent(self.profile.httpUserAgent())
        cookie_store = self.profile.cookieStore()
        cookie_store.cookieAdded.connect(self.tab.fetcher.add_cookie)
        cookie_store.loadAllCookies()
        install_compat_polyfills(self.profile)

        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(6)
        self.browser_tabs = QTabBar()
        self.browser_tabs.setObjectName("BrowserTabs")
        self.browser_tabs.setTabsClosable(True)
        self.browser_tabs.setExpanding(False)
        self.browser_tabs.setDrawBase(False)
        self.browser_tabs.setElideMode(Qt.ElideRight)
        self.browser_tabs.setUsesScrollButtons(True)
        self.browser_tabs.currentChanged.connect(self._browser_tab_changed)
        self.browser_tabs.tabCloseRequested.connect(self.close_tab)
        tabs_row.addWidget(self.browser_tabs, 0)
        # Explicit stretch (rather than stretching the tab bar) so the +
        # button stays on the right even when the strip is hidden.
        tabs_row.addStretch(1)


        self.views = QStackedWidget()
        # Low enough that the browser pane still fits at the smallest window
        # size; the splitter gives it far more than this in practice.
        self.views.setMinimumHeight(160)

        browser_pane = QWidget()
        # The title, address bar and tab strip live in this pane with the web
        # views, so popping it out gives a complete little browser window.
        browser_layout = QVBoxLayout(browser_pane)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.setSpacing(8)
        browser_layout.addWidget(self.page_title)
        browser_layout.addLayout(nav)
        browser_layout.addLayout(tabs_row)
        browser_layout.addWidget(self.views, 1)

        # Gallery pane: collect controls + the image cards
        gallery_pane = QWidget()
        gallery_layout = QVBoxLayout(gallery_pane)
        gallery_layout.setContentsMargins(0, 8, 0, 0)
        gallery_layout.setSpacing(10)

        collect_row = QHBoxLayout()
        collect_row.setSpacing(10)
        self.collect_btn = _primary_button("Collect Images From Page")
        self.collect_btn.clicked.connect(lambda: self.collect_images(scroll=False))
        collect_row.addWidget(self.collect_btn)

        self.deep_btn = _plain_button("Scroll Page + Collect")
        self.deep_btn.setToolTip(
            "Scrolls through the whole page first so lazy-loaded images appear."
        )
        self.deep_btn.clicked.connect(lambda: self.collect_images(scroll=True))
        collect_row.addWidget(self.deep_btn)

        self.status = _muted("Ready")
        self.status.setWordWrap(False)
        collect_row.addWidget(self.status, 1)

        self.errors_button = _plain_button("⚠ Page errors")
        self.errors_button.setToolTip(
            "JavaScript errors reported by the current page.\n"
            "If a site's buttons don't respond, this says why."
        )
        self.errors_button.clicked.connect(self.show_page_errors)
        self.errors_button.setVisible(False)
        collect_row.addWidget(self.errors_button)

        self.count_label = _muted("0 images")
        self.count_label.setWordWrap(False)
        collect_row.addWidget(self.count_label)
        gallery_layout.addLayout(collect_row)

        self.gallery_scroll = QScrollArea()
        self.gallery_scroll.setObjectName("PanelScroll")
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_scroll.setFrameShape(QFrame.NoFrame)
        gallery_host = QWidget()
        self.gallery = QGridLayout(gallery_host)
        self.gallery.setContentsMargins(2, 2, 2, 2)
        self.gallery.setSpacing(14)
        self.gallery.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.gallery_scroll.setWidget(gallery_host)
        gallery_layout.addWidget(self.gallery_scroll, 1)
        gallery_pane.setMinimumHeight(60)

        self.detached_note = QWidget()
        note_layout = QVBoxLayout(self.detached_note)
        note_layout.setContentsMargins(0, 0, 0, 0)
        note_layout.setSpacing(10)
        note_layout.addStretch()
        note_label = _muted(
            "The browser is open in its own window.\n"
            "Close that window to put it back here."
        )
        note_label.setAlignment(Qt.AlignCenter)
        note_layout.addWidget(note_label)
        bring_back = _plain_button("Bring the browser back")
        bring_back.clicked.connect(self.reattach_browser)
        note_row = QHBoxLayout()
        note_row.addStretch()
        note_row.addWidget(bring_back)
        note_row.addStretch()
        note_layout.addLayout(note_row)
        note_layout.addStretch()

        self.browser_pane = browser_pane
        self.browser_holder = QStackedWidget()
        self.browser_holder.addWidget(browser_pane)
        self.browser_holder.addWidget(self.detached_note)

        self.splitter = GripSplitter(Qt.Vertical)
        self.splitter.setObjectName("BrowserSplitter")
        self.splitter.setHandleWidth(14)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.browser_holder)
        self.splitter.addWidget(gallery_pane)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        main.addWidget(self.splitter, 1)

        back.clicked.connect(lambda: self._on_view(lambda view: view.back()))
        forward.clicked.connect(lambda: self._on_view(lambda view: view.forward()))
        reload_button.clicked.connect(lambda: self._on_view(lambda view: view.reload()))
        go.clicked.connect(self.navigate)
        self.url.returnPressed.connect(self.navigate)

        QShortcut(QKeySequence("Ctrl+T"), self, activated=lambda: self.new_tab(focus_url=True))
        QShortcut(QKeySequence("Ctrl+W"), self,
                  activated=lambda: self.close_tab(self.browser_tabs.currentIndex()))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_url_bar)

    # -- browser tabs -------------------------------------------------------

    @property
    def browser(self):
        """The web view in the active browser tab, or None if Qt WebEngine
        isn't available (in which case there are no views at all)."""
        views = getattr(self, "views", None)
        return views.currentWidget() if views is not None else None

    def _on_view(self, action):
        view = self.browser
        if view is not None:
            action(view)

    def _ensure_tab(self):
        """The first tab is created lazily (see showEvent), so anything that
        needs a view has to be able to bring one into being."""
        view = self.browser
        if view is None and WEBENGINE_AVAILABLE:
            view = self.new_tab(QUrl("about:blank"))
        return view

    def _focus_url_bar(self):
        self.url.setFocus()
        self.url.selectAll()

    def _new_view(self):
        view = BrowserView(self)
        view.setPage(LoggingWebPage(self.profile, view, self._record_page_error))
        settings = view.settings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.AutoLoadImages, True)
        settings.setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
        view.page().fullScreenRequested.connect(lambda request: request.accept())
        view.urlChanged.connect(lambda qurl, v=view: self._view_url_changed(v, qurl))
        view.titleChanged.connect(lambda title, v=view: self._view_title_changed(v, title))
        view.iconChanged.connect(lambda icon, v=view: self._view_icon_changed(v, icon))
        view.loadStarted.connect(lambda v=view: self._view_load_started(v))
        view.loadFinished.connect(lambda ok, v=view: self._view_load_finished(v, ok))
        return view

    def new_tab(self, url=None, background=False, focus_url=False):
        """Opens a new browser tab and returns its view. Called both by the +
        button and by the page itself when a link wants a new window."""
        view = self._new_view()
        self.views.addWidget(view)
        index = self.browser_tabs.addTab("New tab")

        if url is not None:
            view.setUrl(url if isinstance(url, QUrl) else QUrl.fromUserInput(str(url)))
        elif not background:
            # A background tab is about to be given its URL by the page that
            # asked for it, so don't overwrite it with a blank page.
            view.setUrl(QUrl("about:blank"))

        if background:
            self.set_status("Opened a background tab.")
        else:
            self.browser_tabs.setCurrentIndex(index)
            self.views.setCurrentWidget(view)
            if focus_url:
                self._focus_url_bar()
        self._update_tab_bar()
        return view

    def close_tab(self, index):
        if index < 0 or index >= self.views.count():
            return
        view = self.views.widget(index)
        self.views.removeWidget(view)
        view.deleteLater()
        self.browser_tabs.removeTab(index)
        if self.browser_tabs.count() == 0:
            self.new_tab(QUrl("about:blank"))
        else:
            self._browser_tab_changed(self.browser_tabs.currentIndex())
        self._update_tab_bar()

    def _update_tab_bar(self):
        # With a single tab there's nothing to switch between, so hide the
        # strip entirely and keep the browser as tall as possible.
        many = self.browser_tabs.count() > 1
        self.browser_tabs.setVisible(many)
        self.browser_tabs.setTabsClosable(many)

    def _browser_tab_changed(self, index):
        if 0 <= index < self.views.count():
            self.views.setCurrentIndex(index)
            view = self.views.widget(index)
            self._url_changed(view.url())
            self._title_changed(view.title())
            view.setFocus()

    def _settings(self):
        return getattr(self.tab.main_window, "settings", None)

    def _remembered_detached(self):
        settings = self._settings()
        if settings is None:
            return False
        return str(settings.value("web_browser_detached", "false")).lower() == "true"

    def _remember_detached(self, detached):
        settings = self._settings()
        if settings is not None:
            settings.setValue("web_browser_detached", "true" if detached else "false")

    def toggle_detached(self):
        if self.detached_window is None:
            self.detach_browser()
        else:
            self.reattach_browser()

    def _update_detach_button(self):
        detached = self.detached_window is not None
        self.detach_button.setText("⤺" if detached else "⧉")
        self.detach_button.setToolTip(
            "Put the browser back in the main window."
            if detached else
            "Open the browser in its own window.\n"
            "Use this if clicking inside the page doesn't work here."
        )

    def detach_browser(self):
        if self.detached_window is not None:
            self.detached_window.raise_()
            self.detached_window.activateWindow()
            return
        self._attached_sizes = self.splitter.sizes()
        self.browser_holder.removeWidget(self.browser_pane)
        self.detached_window = DetachedBrowserWindow(self, self.browser_pane)
        self.detached_window.show()
        self.detached_window.raise_()
        self.detached_window.activateWindow()
        self._update_detach_button()
        self._remember_detached(True)
        self.set_status("Browser opened in its own window.")

    def reattach_browser(self):
        if self.detached_window is None:
            return
        window, self.detached_window = self.detached_window, None
        window.owner = None
        self.browser_holder.insertWidget(0, self.browser_pane)
        self.browser_holder.setCurrentWidget(self.browser_pane)
        self._update_detach_button()
        if getattr(self, "_attached_sizes", None):
            self.splitter.setSizes(self._attached_sizes)
        window.close()
        window.deleteLater()
        self._remember_detached(False)
        self.set_status("Browser back in the main window.")

    def _record_page_error(self, text):
        # Same error repeated by a framework's render loop is noise.
        if text in self.page_errors:
            return
        self.page_errors.append(text)
        del self.page_errors[:-40]
        button = getattr(self, "errors_button", None)
        if button is not None:
            button.setText(f"⚠ {len(self.page_errors)} page error"
                           f"{'' if len(self.page_errors) == 1 else 's'}")
            button.setVisible(True)

    def _clear_page_errors(self):
        self.page_errors = []
        button = getattr(self, "errors_button", None)
        if button is not None:
            button.setVisible(False)

    def show_page_errors(self):
        if not self.page_errors:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Page errors")
        box.setIcon(QMessageBox.Information)
        box.setText(
            "The current page reported these JavaScript errors.\n\n"
            "A site whose scripts fail can render correctly and still refuse "
            "to respond to clicks."
        )
        box.setDetailedText("\n\n".join(self.page_errors))
        box.exec()

    def _view_index(self, view):
        return self.views.indexOf(view)

    def _tab_caption(self, view, title):
        if title and title != "about:blank":
            return title
        host = view.url().host()
        return host or "New tab"

    def _view_url_changed(self, view, qurl):
        index = self._view_index(view)
        if index >= 0:
            self.browser_tabs.setTabToolTip(index, qurl.toString())
        if view is self.browser:
            self._url_changed(qurl)

    def _view_title_changed(self, view, title):
        index = self._view_index(view)
        if index >= 0:
            caption = self._tab_caption(view, title)
            self.browser_tabs.setTabText(
                index, caption if len(caption) <= 24 else caption[:23] + "…"
            )
            self.browser_tabs.setTabToolTip(index, caption)
        if view is self.browser:
            self._title_changed(title)

    def _view_icon_changed(self, view, icon):
        index = self._view_index(view)
        if index >= 0:
            self.browser_tabs.setTabIcon(index, icon)

    def _view_load_started(self, view):
        if view is self.browser:
            self._clear_page_errors()
        index = self._view_index(view)
        if index >= 0 and not self.browser_tabs.tabText(index).strip():
            self.browser_tabs.setTabText(index, "Loading…")
        if view is self.browser:
            self.set_status("Loading webpage…")

    def _view_load_finished(self, view, ok):
        index = self._view_index(view)
        if index >= 0:
            self.browser_tabs.setTabText(
                index, self._tab_caption(view, view.title())[:24]
            )
        if view is self.browser:
            self.page_loaded(ok)

    def showEvent(self, event):
        super().showEvent(event)
        # The first web view is created here rather than in __init__ because
        # this tab is built while it's still hidden behind the others, and a
        # web view created inside a hidden parent doesn't always wire up its
        # input handling properly. Creating it once the tab is actually on
        # screen matches how the standalone browser this came from behaved.
        if WEBENGINE_AVAILABLE and self.views.count() == 0:
            self.new_tab(QUrl("about:blank"))
            # If the browser was last used in its own window, put it back
            # there rather than making the user click the button every time.
            if self._remembered_detached() and self.detached_window is None:
                QTimer.singleShot(0, self.detach_browser)
        # Until something has been collected the gallery is empty, so start
        # it at just its button row and give the browser everything else.
        # receive_images() opens the gallery up once there are cards to show.
        if self.browser is not None and not self._sized_splitter:
            self._sized_splitter = True
            QTimer.singleShot(0, self._initial_split)

    def _initial_split(self):
        total = sum(self.splitter.sizes())
        gallery = self.splitter.widget(1).minimumSizeHint().height()
        self.splitter.setSizes([max(0, total - gallery), gallery])

    def _build_unavailable(self, main):
        """Shown instead of the browser when PyQt6-WebEngine isn't installed."""
        self.status = QLabel("")
        self.count_label = QLabel("")
        box = QGroupBox("Embedded browser unavailable")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(18, 16, 18, 18)
        message = QLabel(
            "The Web Images browser needs Qt WebEngine, which ships as a "
            "separate package.\n\n"
            "Install it with:\n"
            "    pip install PyQt6-WebEngine\n\n"
            "The Overlay Studio next door still works without it — you can "
            "import images from your own disk there.\n\n"
            f"Reported error: {WEBENGINE_IMPORT_ERROR}"
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        layout.addStretch()
        main.addWidget(box, 1)

    # -- navigation ---------------------------------------------------------

    def navigate(self):
        text = self.url.text().strip()
        if not text:
            return
        view = self._ensure_tab()
        if view is None:
            return
        if "://" not in text and not text.startswith(("about:", "file:")):
            if " " in text or "." not in text:
                text = "https://duckduckgo.com/?q=" + quote(text)
            else:
                text = "https://" + text
        view.setUrl(QUrl.fromUserInput(text))

    def _url_changed(self, qurl):
        text = qurl.toString()
        if text != "about:blank":
            self.url.setText(text)

    def _title_changed(self, title):
        if title and title != "about:blank":
            self.page_title.setText(title)
        else:
            self.page_title.setText(self.DEFAULT_TITLE)

    def page_loaded(self, ok):
        self.set_status(
            "Page loaded — click Collect Images to scan it." if ok else "Page failed to load."
        )

    # -- collecting ---------------------------------------------------------

    def _set_busy(self, busy):
        self.collect_btn.setEnabled(not busy)
        self.deep_btn.setEnabled(not busy)

    def collect_images(self, scroll=False):
        if self._ensure_tab() is None:
            return
        if self.browser.url().toString() in ("", "about:blank"):
            self.set_status("Open a webpage first.")
            return
        self._set_busy(True)
        if scroll:
            self.set_status("Scrolling page to trigger lazy-loaded images…")
            self._scroll_polls = 0
            self.browser.page().runJavaScript(SCROLL_JS, lambda _result: self._poll_scroll())
        else:
            self._run_collect()

    def _poll_scroll(self):
        def check(done):
            self._scroll_polls += 1
            if done is True or self._scroll_polls > 150:  # ~45 s safety limit
                self._run_collect()
            else:
                self._poll_scroll()

        QTimer.singleShot(300, lambda: self.browser.page().runJavaScript(
            "window.__wibScrollDone === true", check))

    def _run_collect(self):
        self.set_status("Scanning page…")
        self.browser.page().runJavaScript(COLLECT_JS, self.receive_images)

    def _gallery_columns(self):
        width = self.gallery_scroll.viewport().width()
        return max(1, (width - 4) // (WEB_CARD_WIDTH + self.gallery.spacing()))

    def receive_images(self, result):
        self._set_busy(False)
        try:
            urls = json.loads(result) if isinstance(result, str) else []
        except (ValueError, TypeError):
            urls = []

        self.scan_id += 1  # invalidates preview jobs from the previous scan
        self.tab.fetcher.referer = self.browser.url().toString()
        self.tab.fetcher.clear_cache()
        self.images = urls
        self.clear_gallery()

        count = len(urls)
        self.count_label.setText(f"{count} image" + ("" if count == 1 else "s"))
        if not urls:
            self.set_status("No image URLs found. Try “Scroll Page + Collect”.")
            return

        # Make sure at least one full row of cards is visible once there's
        # something to look at - the user can still drag the bar afterwards.
        sizes = self.splitter.sizes()
        total = sum(sizes)
        gallery = min(330, total - 260)  # keep at least 260px of browser
        if sizes[1] < gallery:
            self.splitter.setSizes([total - gallery, gallery])

        self.preview_pending = count
        self.preview_failed = 0
        columns = self._gallery_columns()
        for index, url in enumerate(urls):
            self.gallery.addWidget(
                WebImageCard(url, index, self), index // columns, index % columns
            )

        self.set_status(f"Found {count} image URL{'s' if count != 1 else ''}. Loading previews…")

    def preview_done(self, scan, ok):
        if scan != self.scan_id:
            return
        self.preview_pending -= 1
        if not ok:
            self.preview_failed += 1
        if self.preview_pending <= 0:
            count = len(self.images)
            message = f"Found {count} image URL{'s' if count != 1 else ''}."
            if self.preview_failed:
                message += f" {self.preview_failed} couldn't be loaded."
            self.set_status(message)

    def clear_gallery(self):
        while self.gallery.count():
            item = self.gallery.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_status(self, text):
        self.status.setText(text)

    def shutdown(self):
        self.scan_id += 1
        if self.detached_window is not None:
            window, self.detached_window = self.detached_window, None
            window.owner = None
            window.close()
        # Stop anything still loading in any tab so closing the app doesn't
        # leave page loads running in the background.
        views = getattr(self, "views", None)
        if views is not None:
            for index in range(views.count()):
                views.widget(index).stop()


# -- Overlay Studio page ----------------------------------------------------

OVERLAY_PRESETS = [
    ("Top Left", "l", "t"), ("Top Center", "c", "t"), ("Top Right", "r", "t"),
    ("Middle Left", "l", "m"), ("Center", "c", "m"), ("Middle Right", "r", "m"),
    ("Bottom Left", "l", "b"), ("Bottom Center", "c", "b"), ("Bottom Right", "r", "b"),
]


class LayerSlot(QFrame):
    """Import box for one layer. Accepts file / image drag-and-drop."""

    def __init__(self, title, on_import, on_path, on_clear):
        super().__init__()
        self.on_path = on_path
        self.setObjectName("Slot")
        self.setProperty("dragOver", "false")
        self.setAcceptDrops(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.thumb = QLabel("＋")
        self.thumb.setObjectName("PreviewBox")
        self.thumb.setFixedSize(64, 64)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("font-size: 22px;")
        layout.addWidget(self.thumb, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(4)
        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 700;")
        column.addWidget(heading)
        self.name = _muted("Drop an image here or click Import", hint=True)
        column.addWidget(self.name)
        column.addSpacing(4)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        import_button = _primary_button("Import…")
        import_button.clicked.connect(on_import)
        buttons.addWidget(import_button)
        self.clear_btn = _plain_button("Clear")
        self.clear_btn.clicked.connect(on_clear)
        self.clear_btn.setEnabled(False)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch()
        column.addLayout(buttons)
        layout.addLayout(column, 1)

    def _set_highlight(self, on):
        self.setProperty("dragOver", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_layer(self, layer):
        if layer is None:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("＋")
            self.name.setText("Drop an image here or click Import")
            self.clear_btn.setEnabled(False)
            return
        pixmap = layer.pixmaps[0].scaled(
            58, 58, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.thumb.setPixmap(pixmap)
        width, height = layer.size
        extra = f"  •  {len(layer.frames)} frames" if layer.animated else ""
        self.name.setText(f"{layer.name}\n{width} × {height}{extra}")
        self.clear_btn.setEnabled(True)

    def set_loading(self, text):
        self.name.setText(text)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasImage():
            event.acceptProposedAction()
            self._set_highlight(True)

    def dragLeaveEvent(self, event):
        self._set_highlight(False)

    def dropEvent(self, event):
        self._set_highlight(False)
        mime = event.mimeData()
        if mime.hasUrls():
            url = mime.urls()[0]
            self.on_path(url.toLocalFile() if url.isLocalFile() else url.toString())
            event.acceptProposedAction()
        elif mime.hasImage():
            image = QImage(mime.imageData())
            array = QByteArray()
            buffer = QBuffer(array)
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            buffer.close()
            data = bytes(array.data())
            self.on_path(lambda: data)
            event.acceptProposedAction()


class ForefrontItem(QGraphicsPixmapItem):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsFocusable | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setTransformationMode(Qt.SmoothTransformation)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag to move • Arrow keys nudge (Shift = 10 px)")

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene() is not None:
            return self.page.clamp_pos(value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.page.on_fg_moved()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self.setCursor(Qt.ClosedHandCursor)
        self.setFocus()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        step = 10 if event.modifiers() & Qt.ShiftModifier else 1
        dx, dy = {
            Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0),
            Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step),
        }.get(event.key(), (None, None))
        if dx is None:
            super().keyPressEvent(event)
            return
        self.setPos(self.pos() + QPointF(dx, dy))


class PreviewView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setObjectName("OverlayPreview")
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(320, 260)
        self.apply_theme()

    def apply_theme(self):
        # The canvas itself is painted by QGraphicsView from this brush, not
        # the stylesheet, so it's set in code.
        self.setBackgroundBrush(QColor(WEB_COLORS["BG"]))

    def fit(self):
        rect = self.sceneRect()
        if rect.isEmpty():
            return
        pad = max(rect.width(), rect.height()) * 0.03
        self.fitInView(rect.adjusted(-pad, -pad, pad, pad), Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class OverlayStudioPage(QWidget):
    """Composite a forefront image over a background, keeping animation."""

    PANEL_WIDTH = 400

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.bg = None
        self.fg = None
        self.bg_item = None
        self.fg_item = None
        self.anchor = ("c", "m")
        self.fg_pos = None
        self._programmatic = False
        self._syncing = False
        self._load_token = {"bg": 0, "fg": 0}

        self.clock = QElapsedTimer()
        self.clock.start()
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(20)
        self.anim_timer.timeout.connect(self._tick)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        # ---- left: controls, grouped like the rest of the app ---------------
        panel_host = QWidget()
        panel = QVBoxLayout(panel_host)
        panel.setContentsMargins(0, 0, 12, 4)
        panel.setSpacing(14)

        panel.addWidget(self._build_layers_group())
        panel.addWidget(self._build_position_group())
        panel.addWidget(self._build_look_group())
        panel.addWidget(self._build_export_group())
        panel.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(panel_host)
        scroll.setFixedWidth(self.PANEL_WIDTH)
        root.addWidget(scroll)

        # ---- right: preview -------------------------------------------------
        preview_box = QGroupBox("Preview")
        right = QVBoxLayout(preview_box)
        right.setContentsMargins(16, 14, 16, 16)
        right.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(_muted(
            "Drag the forefront to move it, or click it and use the arrow keys "
            "(Shift = 10 px)."
        ), 1)
        self.status = _muted("")
        self.status.setWordWrap(False)
        head.addWidget(self.status)
        right.addLayout(head)

        self.scene = QGraphicsScene(self)
        self.view = PreviewView(self.scene)
        right.addWidget(self.view, 1)
        root.addWidget(preview_box, 1)

        self.rebuild_scene()

    # -- panel sections -----------------------------------------------------

    @staticmethod
    def _group(title):
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        return box, layout

    def _build_layers_group(self):
        box, layout = self._group("Layers")
        self.bg_slot = LayerSlot(
            "Background image",
            lambda: self.pick_file("bg"),
            lambda path: self.load_path("bg", path),
            lambda: self.clear_layer("bg"),
        )
        self.fg_slot = LayerSlot(
            "Forefront image",
            lambda: self.pick_file("fg"),
            lambda path: self.load_path("fg", path),
            lambda: self.clear_layer("fg"),
        )
        layout.addWidget(self.bg_slot)
        layout.addWidget(self.fg_slot)
        swap = _plain_button("⇅  Swap layers")
        swap.clicked.connect(self.swap_layers)
        layout.addWidget(swap)
        return box

    def _build_position_group(self):
        box, layout = self._group("Position")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.preset_buttons = {}
        for index, (label, horizontal, vertical) in enumerate(OVERLAY_PRESETS):
            button = _plain_button(label, "ChipButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, anchor=(horizontal, vertical): self.apply_preset(anchor)
            )
            grid.addWidget(button, index // 3, index % 3)
            self.preset_buttons[(horizontal, vertical)] = button
        layout.addLayout(grid)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.addWidget(QLabel("Edge margin"), 0, 0)
        self.margin = QSpinBox()
        self.margin.setRange(0, 5000)
        self.margin.setValue(20)
        self.margin.setSuffix(" px")
        self.margin.valueChanged.connect(self._reapply_anchor)
        form.addWidget(self.margin, 0, 1, 1, 3)

        form.addWidget(QLabel("X"), 1, 0)
        self.x_spin = QSpinBox()
        form.addWidget(self.x_spin, 1, 1)
        form.addWidget(QLabel("Y"), 1, 2, Qt.AlignRight)
        self.y_spin = QSpinBox()
        form.addWidget(self.y_spin, 1, 3)
        for spin in (self.x_spin, self.y_spin):
            spin.setRange(-100000, 100000)
            spin.setSuffix(" px")
            spin.valueChanged.connect(self._spin_moved)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        layout.addLayout(form)

        self.keep_inside = QCheckBox("Keep forefront inside the background")
        self.keep_inside.setChecked(True)
        self.keep_inside.toggled.connect(lambda _value: self._nudge_into_bounds())
        layout.addWidget(self.keep_inside)
        return box

    def _build_look_group(self):
        box, layout = self._group("Size & look")
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(12)

        form.addWidget(QLabel("Scale"), 0, 0)
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(1, 400)
        self.scale_slider.setValue(100)
        form.addWidget(self.scale_slider, 0, 1)
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 1000)
        self.scale_spin.setValue(100)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setMinimumWidth(84)
        form.addWidget(self.scale_spin, 0, 2)
        self.scale_slider.valueChanged.connect(self.scale_spin.setValue)
        self.scale_spin.valueChanged.connect(self._scale_changed)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        for label, fraction in (("¼ width", 0.25), ("⅓ width", 1 / 3),
                                ("½ width", 0.5), ("100%", None)):
            button = _plain_button(label, "ChipButton")
            button.clicked.connect(lambda _checked=False, f=fraction: self.quick_scale(f))
            quick_row.addWidget(button)
        form.addLayout(quick_row, 1, 1, 1, 2)

        form.addWidget(QLabel("Opacity"), 2, 0)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(100)
        form.addWidget(self.opacity, 2, 1)
        self.opacity_lbl = QLabel("100%")
        self.opacity_lbl.setMinimumWidth(84)
        self.opacity_lbl.setAlignment(Qt.AlignCenter)
        form.addWidget(self.opacity_lbl, 2, 2)
        self.opacity.valueChanged.connect(self._opacity_changed)
        form.setColumnStretch(1, 1)
        layout.addLayout(form)
        return box

    def _build_export_group(self):
        box, layout = self._group("Export")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.out_format = QComboBox()
        self.out_format.addItems(web_output_formats())
        self.out_format.currentTextChanged.connect(lambda _text: self._update_info())
        row.addWidget(self.out_format, 1)
        self.save_btn = _primary_button("Save Image…")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn, 1)
        layout.addLayout(row)
        self.info = _muted("", hint=True)
        layout.addWidget(self.info)
        return box

    # -- loading ------------------------------------------------------------

    def pick_file(self, role):
        title = "Choose background image" if role == "bg" else "Choose forefront image"
        path, _ = QFileDialog.getOpenFileName(
            self, title, self.tab.last_open_dir, WEB_IMAGE_FILTER
        )
        if path:
            self.tab.last_open_dir = os.path.dirname(path)
            self.load_path(role, path)

    def load_path(self, role, path):
        if callable(path):  # raw bytes provider (dropped image data)
            self.load_async(role, path, "dropped image")
            return
        if path.startswith(("http://", "https://", "data:")):
            name = "web image" if path.startswith("data:") else \
                os.path.splitext(os.path.basename(urlparse(path).path))[0] or "web image"
            self.load_async(role, lambda: self.tab.fetcher.get(path), name)
            return

        def read():
            with open(path, "rb") as handle:
                return handle.read()
        self.load_async(role, read, os.path.basename(path))

    def load_async(self, role, fetch, name):
        """fetch() -> bytes runs on a worker thread; the result lands on the GUI thread."""
        self._load_token[role] += 1
        token = self._load_token[role]
        slot = self.bg_slot if role == "bg" else self.fg_slot
        slot.set_loading(f"Loading {name}…")

        def work():
            try:
                layer = build_layer(fetch(), name)
                error = None
            except Exception as exc:  # noqa: BLE001
                layer, error = None, short_error(exc)
            self.tab.bridge.call.emit(
                lambda: self._layer_ready(role, token, layer, error)
            )

        self.tab.pool.submit(work)

    def _layer_ready(self, role, token, layer, error):
        if token != self._load_token[role]:
            return  # a newer import replaced this one
        if error:
            (self.bg_slot if role == "bg" else self.fg_slot).set_layer(getattr(self, role))
            self.set_status("")
            show_error(self, "Couldn't load image", error)
            return
        layer.pixmaps = [QPixmap.fromImage(image) for image in layer.qimages]
        layer.qimages = []
        if role == "bg":
            self.bg = layer
            self.bg_slot.set_layer(layer)
        else:
            self.fg = layer
            self.fg_slot.set_layer(layer)
            self.fg_pos = None
            if self.anchor is None:
                self.anchor = ("c", "m")
            self._auto_fit_scale()
        self.set_status(f"Loaded {layer.name}")
        self.rebuild_scene()

    def clear_layer(self, role):
        self._load_token[role] += 1
        setattr(self, role, None)
        (self.bg_slot if role == "bg" else self.fg_slot).set_layer(None)
        self.rebuild_scene()

    def swap_layers(self):
        self.bg, self.fg = self.fg, self.bg
        self.bg_slot.set_layer(self.bg)
        self.fg_slot.set_layer(self.fg)
        self.fg_pos = None
        self.anchor = self.anchor or ("c", "m")
        self._auto_fit_scale()
        self.rebuild_scene()

    def _auto_fit_scale(self):
        """New forefront starts at 100%, or half the background if it's bigger."""
        percent = 100
        if self.bg and self.fg:
            bg_width, bg_height = self.bg.size
            fg_width, fg_height = self.fg.size
            if fg_width > bg_width or fg_height > bg_height:
                percent = max(1, int(min(bg_width / fg_width, bg_height / fg_height) * 50))
        self._set_scale_controls(percent)

    # -- scene --------------------------------------------------------------

    def rebuild_scene(self):
        self.anim_timer.stop()
        self.bg_item = None
        self.fg_item = None
        self.scene.clear()

        if not self.bg:
            text = self.scene.addText("Import a background image to start")
            text.setDefaultTextColor(QColor(WEB_COLORS["MUTED"]))
            self.scene.setSceneRect(QRectF(-200, -40, 600, 120))
            text.setPos(0, 0)
            self.view.fit()
            self._update_controls()
            return

        width, height = self.bg.size
        rect = QRectF(0, 0, width, height)
        checker = QGraphicsRectItem(rect)
        checker.setBrush(checker_brush())
        checker.setPen(QPen(Qt.NoPen))
        checker.setZValue(0)
        self.scene.addItem(checker)

        self.bg_item = QGraphicsPixmapItem(self.bg.pixmaps[0])
        self.bg_item.setZValue(1)
        self.bg_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.bg_item)

        outline = QGraphicsRectItem(rect)
        outline.setPen(QPen(QColor(WEB_COLORS["BORDER"])))
        outline.setZValue(3)
        self.scene.addItem(outline)

        if self.fg:
            self.fg_item = ForefrontItem(self)
            self.fg_item.setPixmap(self.fg.pixmaps[0])
            self.fg_item.setScale(self.scale_spin.value() / 100)
            self.fg_item.setOpacity(self.opacity.value() / 100)
            self.fg_item.setZValue(2)
            self.scene.addItem(self.fg_item)
            if self.anchor is not None or self.fg_pos is None:
                self.apply_preset(self.anchor or ("c", "m"))
            else:
                self._move_fg(*self.fg_pos)
            self.fg_item.setSelected(True)

        self.scene.setSceneRect(rect)
        self.view.fit()
        if self.bg.animated or (self.fg and self.fg.animated):
            self.anim_timer.start()
        self._update_controls()

    def _tick(self):
        elapsed = self.clock.elapsed()
        if self.bg_item is not None and self.bg.animated:
            self.bg_item.setPixmap(self.bg.pixmaps[self.bg.index_at(elapsed)])
        if self.fg_item is not None and self.fg.animated:
            self.fg_item.setPixmap(self.fg.pixmaps[self.fg.index_at(elapsed)])

    def _fg_display_size(self):
        width, height = self.fg.size
        scale = self.scale_spin.value() / 100
        return width * scale, height * scale

    def clamp_pos(self, value):
        x, y = round(value.x()), round(value.y())
        if self.keep_inside.isChecked() and self.bg and self.fg:
            bg_width, bg_height = self.bg.size
            fg_width, fg_height = self._fg_display_size()
            low_x, high_x = sorted((0, round(bg_width - fg_width)))
            low_y, high_y = sorted((0, round(bg_height - fg_height)))
            x = min(max(x, low_x), high_x)
            y = min(max(y, low_y), high_y)
        return QPointF(x, y)

    def _move_fg(self, x, y):
        if self.fg_item is None:
            return
        self._programmatic = True
        try:
            self.fg_item.setPos(QPointF(x, y))
        finally:
            self._programmatic = False
        self.on_fg_moved(programmatic=True)

    def on_fg_moved(self, programmatic=None):
        if self.fg_item is None:
            return
        if programmatic is None:
            programmatic = self._programmatic
        position = self.fg_item.pos()
        self.fg_pos = (position.x(), position.y())
        if not programmatic:
            self.anchor = None
        self._syncing = True
        self.x_spin.setValue(round(position.x()))
        self.y_spin.setValue(round(position.y()))
        self._syncing = False
        self._update_preset_buttons()
        self._update_info()

    def _spin_moved(self):
        if self._syncing or self.fg_item is None:
            return
        self.anchor = None
        self._move_fg(self.x_spin.value(), self.y_spin.value())
        self._update_preset_buttons()

    def _nudge_into_bounds(self):
        if self.fg_item is not None:
            if self.anchor:
                self.apply_preset(self.anchor)
            else:
                self._move_fg(*self.fg_pos)

    # -- presets & scale ----------------------------------------------------

    def apply_preset(self, anchor):
        self.anchor = anchor
        if not (self.bg and self.fg and self.fg_item is not None):
            self._update_preset_buttons()
            return
        bg_width, bg_height = self.bg.size
        fg_width, fg_height = self._fg_display_size()
        margin = self.margin.value()
        horizontal, vertical = anchor
        x = {"l": margin, "c": (bg_width - fg_width) / 2,
             "r": bg_width - fg_width - margin}[horizontal]
        y = {"t": margin, "m": (bg_height - fg_height) / 2,
             "b": bg_height - fg_height - margin}[vertical]
        self._move_fg(x, y)
        self.anchor = anchor
        self._update_preset_buttons()

    def _reapply_anchor(self):
        if self.anchor:
            self.apply_preset(self.anchor)

    def _update_preset_buttons(self):
        for key, button in self.preset_buttons.items():
            button.setChecked(key == self.anchor)

    def _set_scale_controls(self, percent):
        self.scale_spin.setValue(percent)  # triggers _scale_changed

    def quick_scale(self, fraction):
        if not self.fg:
            return
        if fraction is None:
            self._set_scale_controls(100)
        elif self.bg:
            self._set_scale_controls(
                max(1, round(self.bg.size[0] * fraction / self.fg.size[0] * 100))
            )

    def _scale_changed(self, percent):
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(min(percent, self.scale_slider.maximum()))
        self.scale_slider.blockSignals(False)
        if self.fg_item is None:
            return
        old_scale = self.fg_item.scale()
        fg_width, fg_height = self.fg.size
        center = self.fg_item.pos() + QPointF(fg_width * old_scale / 2,
                                              fg_height * old_scale / 2)
        self.fg_item.setScale(percent / 100)
        if self.anchor:
            self.apply_preset(self.anchor)
        else:
            new_scale = percent / 100
            self._move_fg(center.x() - fg_width * new_scale / 2,
                          center.y() - fg_height * new_scale / 2)
        self._update_info()

    def _opacity_changed(self, value):
        self.opacity_lbl.setText(f"{value}%")
        if self.fg_item is not None:
            self.fg_item.setOpacity(value / 100)

    # -- info / state -------------------------------------------------------

    def _update_controls(self):
        has_fg = self.fg_item is not None
        for widget in (self.x_spin, self.y_spin, self.scale_slider, self.scale_spin,
                       self.opacity, self.margin, *self.preset_buttons.values()):
            widget.setEnabled(has_fg)
        self.save_btn.setEnabled(self.bg is not None)
        self._update_preset_buttons()
        self._update_info()

    def _update_info(self):
        if not self.bg:
            self.info.setText("Output uses the background's size.")
            return
        width, height = self.bg.size
        parts = [f"Output: {width} × {height}"]
        if self.fg_item is not None:
            fg_width, fg_height = self._fg_display_size()
            parts.append(f"Forefront: {round(fg_width)} × {round(fg_height)}")
        animated = self.bg.animated or (self.fg is not None and self.fg.animated)
        if animated:
            if self.out_format.currentText() == "PNG":
                parts.append(
                    "PNG saves a still of the first frame — choose GIF, WebP or "
                    "AVIF to keep animation."
                )
            else:
                parts.append("Animation will be kept.")
        self.info.setText("\n".join(parts))

    def set_status(self, text):
        self.status.setText(text)

    def on_theme_changed(self):
        self.view.apply_theme()
        self.rebuild_scene()

    # -- export -------------------------------------------------------------

    def save(self):
        if not self.bg:
            return
        fmt = self.out_format.currentText().lower()
        suffix = "." + fmt
        base = os.path.splitext(self.bg.name)[0] or "overlay"
        default = os.path.join(self.tab.last_save_dir, f"{base}_overlay{suffix}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Overlay", default, f"{fmt.upper()} Image (*{suffix})"
        )
        if not path:
            return
        if not path.lower().endswith(suffix):
            path += suffix
        self.tab.last_save_dir = os.path.dirname(path)

        bg, fg = self.bg, self.fg if self.fg_item is not None else None
        x, y = self.fg_pos if fg else (0, 0)
        scale = self.scale_spin.value() / 100
        opacity = self.opacity.value() / 100

        self.save_btn.setEnabled(False)
        self.save_btn.setText("Saving…")
        self.set_status("Rendering…")

        def work():
            error = None
            try:
                frames, durations = compose_frames(
                    bg, fg, x, y, scale, opacity, animate=(fmt != "png")
                )
                save_frames(frames, durations, path, fmt)
            except Exception as exc:  # noqa: BLE001
                error = short_error(exc)

            def done():
                self.save_btn.setEnabled(True)
                self.save_btn.setText("Save Image…")
                if error:
                    self.set_status("")
                    show_critical(self, "Save failed", error)
                else:
                    self.set_status(f"Saved {os.path.basename(path)}")
            self.tab.bridge.call.emit(done)

        threading.Thread(target=work, daemon=True).start()

    def shutdown(self):
        self.anim_timer.stop()


# -- The tab itself ---------------------------------------------------------

class WebImagesTab(QWidget):
    """Container for the Browser and Overlay Studio pages, with its own
    secondary nav bar."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.bridge = UiBridge()
        self.fetcher = WebFetcher()
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.last_save_dir = os.path.expanduser("~")
        self.last_open_dir = os.path.expanduser("~")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 12, 22, 18)
        root.setSpacing(12)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self.stack = QStackedWidget()
        self.nav_buttons = []

        self.browser_page = WebBrowserPage(self)
        self.overlay_page = OverlayStudioPage(self)
        pages = (("🌐  Browser", self.browser_page),
                 ("🖼  Overlay Studio", self.overlay_page))
        for index, (label, page) in enumerate(pages):
            self.stack.addWidget(page)
            button = _plain_button(label, "SubNavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
            nav_row.addWidget(button)
            self.nav_buttons.append(button)
        nav_row.addStretch()
        root.addLayout(nav_row)

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        root.addWidget(divider)
        root.addWidget(self.stack, 1)

        self.show_page(0)

    def apply_theme(self):
        """Widgets follow the app-wide stylesheet on their own; only the
        Overlay Studio's code-painted canvas needs refreshing."""
        self.overlay_page.on_theme_changed()

    def show_page(self, index):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        if index == 1:
            QTimer.singleShot(0, self.overlay_page.view.fit)

    def shutdown(self):
        self.browser_page.shutdown()
        self.overlay_page.shutdown()
        self.pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# WebP Flipbook tab
#
# Merged in from the standalone "WebP Flipbook Converter", which was written
# with customtkinter. The grid-detection maths is carried over as-is; the UI
# is rebuilt in PyQt6 so it matches the rest of the app, and the heavy work
# runs on this app's shared thread pool instead of raw threads.
#
# Handles both kinds of source:
#   - a normal animated WebP  -> GIF, keeping the original frame timings
#   - a flipbook/sprite sheet -> sliced into frames, then encoded as a GIF
# ---------------------------------------------------------------------------

FLIPBOOK_DEFAULT_FPS = 12
FLIPBOOK_MAX_PREVIEW_FRAMES = 200
FLIPBOOK_GRID_OVERLAY_COLOR = "#7a5cff"


def _flipbook_boundary_profile(image, axis):
    """Mean colour change between neighbouring rows/columns - sprite sheet
    seams show up as spikes in this."""
    max_side = 900
    scale = min(1.0, max_side / max(image.size))
    if scale < 1:
        small = image.resize(
            (max(2, int(image.width * scale)), max(2, int(image.height * scale))),
            Image.BILINEAR if not hasattr(Image, "Resampling") else Image.Resampling.BILINEAR,
        )
    else:
        small = image.copy()

    array = np.asarray(small.convert("RGB"), dtype="int16")
    if axis == "x":
        return np.abs(array[:, 1:] - array[:, :-1]).mean(axis=(0, 2))
    return np.abs(array[1:] - array[:-1]).mean(axis=(1, 2))


def _flipbook_repeating_period(profile, original_length):
    """Find a strong repeating period using autocorrelation.

    The profile may be downsampled, so candidate periods are generated in
    ORIGINAL pixels and then mapped back into profile coordinates."""
    if len(profile) < 8:
        return None

    values = profile.astype(float)
    values -= values.mean()
    correlation = np.correlate(values, values, mode="full")[len(values) - 1:]

    scale = len(profile) / float(original_length)
    max_rows = min(24, original_length // 24)

    best_rows = None
    best_score = -1e30

    for rows in range(1, max_rows + 1):
        if original_length % rows != 0:
            continue

        tile_size = original_length / rows
        if tile_size < 24:
            continue

        period = int(round(tile_size * scale))
        if period < 2 or period >= len(correlation):
            continue

        score = float(correlation[period])

        # Prefer sheets with multiple reasonably sized frames, while still
        # allowing a single-frame image.
        if rows == 1:
            score *= 0.45
        elif rows == 2:
            score *= 0.75

        # Very tiny cells are unlikely to be intended animation frames.
        if tile_size < 48:
            score *= 0.5

        if score > best_score:
            best_score = score
            best_rows = rows

    if best_rows is None or best_rows == 1:
        return None

    return original_length // best_rows


def detect_flipbook_grid(source_image, is_animated):
    """Returns (cols, rows, reason) for the given source image."""
    if source_image is None:
        raise ValueError("Select a WebP first.")

    if is_animated:
        return 1, 1, "native animated WebP"

    if not NUMPY_AVAILABLE:
        raise RuntimeError(
            "Automatic grid detection needs NumPy - run: pip install numpy\n\n"
            "You can still type the columns and rows in manually."
        )

    image = source_image.convert("RGB")
    width, height = image.size

    profile_x = _flipbook_boundary_profile(image, "x")
    profile_y = _flipbook_boundary_profile(image, "y")

    def strong_split(profile, original_length):
        """Look for a strong central split - catches common 2-column sheets."""
        if len(profile) < 4:
            return 1
        median = float(np.median(profile)) + 1e-6
        threshold = max(float(np.percentile(profile, 97)), median * 2.2)
        peaks = []
        for i in range(1, len(profile) - 1):
            if profile[i] >= profile[i - 1] and profile[i] >= profile[i + 1]:
                if profile[i] >= threshold:
                    position = (i + 1) / len(profile) * original_length
                    peaks.append((profile[i], position))
        # Prefer boundaries near a clean equal split.
        best_columns = 1
        best_value = 0
        for value, position in peaks:
            for columns in range(2, 9):
                expected = original_length * round(
                    position / (original_length / columns)
                ) / columns
                error = abs(position - expected) / original_length
                if error < 0.012 and value > best_value:
                    best_value = value
                    best_columns = columns
        return best_columns

    columns = strong_split(profile_x, width)

    # For rows, autocorrelation is particularly effective for a repeating
    # flipbook sheet such as 2 x 8, where each row has a similar height.
    period_y = _flipbook_repeating_period(profile_y, height)
    rows = round(height / period_y) if period_y else 1

    # Keep the result sane.
    columns = max(1, min(columns, 12))
    rows = max(1, min(rows, 24))

    # If the inferred cells are tiny, fall back to treating it as one frame.
    if width / columns < 32 or height / rows < 32:
        columns, rows = 1, 1

    return columns, rows, "sprite/flipbook grid detected"


def flipbook_extract_frames(settings, report=None):
    """Open the source and return (frames, durations, kind).

    Runs on a worker thread, so it re-opens the file itself rather than
    sharing a PIL image with the GUI thread."""
    path = settings["path"]
    source = Image.open(path)
    animated = getattr(source, "n_frames", 1) > 1

    if animated:
        frames, durations = [], []
        for frame in ImageSequence.Iterator(source):
            frames.append(frame.convert("RGBA").copy())
            durations.append(max(20, int(frame.info.get("duration", 100) or 100)))
        return frames, durations, "native"

    columns, rows = settings["cols"], settings["rows"]
    if not columns or not rows:
        columns, rows, _ = detect_flipbook_grid(source, False)

    if columns < 1 or rows < 1:
        raise ValueError("Rows and columns must be at least 1.")

    image = source.convert("RGBA")
    frame_width = image.width // columns
    frame_height = image.height // rows

    if frame_width * columns != image.width or frame_height * rows != image.height:
        raise ValueError(
            f"The selected grid ({columns} × {rows}) does not divide the image "
            f"exactly.\nImage size: {image.width} × {image.height}"
        )

    frames = []
    if settings["order"] == "row":
        order = [(r, c) for r in range(rows) for c in range(columns)]
    else:
        order = [(r, c) for c in range(columns) for r in range(rows)]

    for row, column in order:
        frames.append(image.crop((
            column * frame_width,
            row * frame_height,
            (column + 1) * frame_width,
            (row + 1) * frame_height,
        )))

    fps = settings["fps"]
    if fps <= 0:
        raise ValueError("FPS must be greater than zero.")
    duration = max(1, int(round(1000 / fps)))
    if report:
        report(f"Sliced {len(frames)} frames from a {columns} × {rows} sheet.")
    return frames, [duration] * len(frames), "sprite"


# A pixel is treated as see-through below this alpha. GIF transparency is
# all-or-nothing - there are no partial alpha values - so semi-transparent
# edge pixels have to fall on one side or the other.
FLIPBOOK_ALPHA_THRESHOLD = 128
FLIPBOOK_TRANSPARENT_INDEX = 255


def frames_have_transparency(frames):
    """True if any frame actually has see-through pixels."""
    for frame in frames:
        if frame.mode != "RGBA":
            continue
        alpha = frame.getchannel("A")
        if alpha.getextrema()[0] < FLIPBOOK_ALPHA_THRESHOLD:
            return True
    return False


def quantize_frame(frame, keep_transparency, background=(255, 255, 255)):
    """RGBA frame -> palette image ready for GIF.

    With transparency kept, one palette slot is reserved as the see-through
    colour and every pixel under the alpha threshold is pointed at it.
    Otherwise the frame is flattened onto a solid background first, which is
    what GIF viewers would otherwise show as black."""
    rgba = frame.convert("RGBA")

    if not keep_transparency:
        flattened = Image.new("RGBA", rgba.size, background + (255,))
        flattened.alpha_composite(rgba)
        return flattened.convert("RGB").quantize(
            colors=256, method=QUANTIZE_MEDIANCUT, dither=DITHER_FLOYDSTEINBERG
        ), None

    # Leave the last palette entry free for "transparent".
    transparent_mask = rgba.getchannel("A").point(
        lambda value: 255 if value < FLIPBOOK_ALPHA_THRESHOLD else 0
    )
    # Composite onto a copy of itself with alpha removed so the quantiser
    # never sees the undefined colours hiding under fully transparent pixels.
    opaque = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    opaque.alpha_composite(rgba)
    palette_image = opaque.convert("RGB").quantize(
        colors=FLIPBOOK_TRANSPARENT_INDEX, method=QUANTIZE_MEDIANCUT,
        dither=DITHER_FLOYDSTEINBERG,
    )
    palette_image.paste(FLIPBOOK_TRANSPARENT_INDEX, transparent_mask)
    return palette_image, FLIPBOOK_TRANSPARENT_INDEX


def flipbook_resize_frames(frames, size):
    if not size:
        return frames
    width, height = size
    if width < 1 or height < 1:
        raise ValueError("Resize dimensions must be greater than zero.")
    return [frame.resize((width, height), RESAMPLE_LANCZOS) for frame in frames]


def _flipbook_save_png_frames(frames, output_dir, base, signals=None, start=0.0, span=1.0):
    folder = os.path.join(output_dir, base + "_frames")
    os.makedirs(folder, exist_ok=True)
    for index, frame in enumerate(frames, start=1):
        frame.save(os.path.join(folder, f"frame_{index:04d}.png"))
        if signals:
            signals.progress.emit(start + span * (index / len(frames)))
    return folder


def flipbook_gif_job(settings, signals):
    signals.status.emit("Extracting frames…")
    frames, durations, _kind = flipbook_extract_frames(
        settings, report=signals.status.emit
    )
    frames = flipbook_resize_frames(frames, settings["resize"])

    if not frames:
        raise ValueError("No frames were found.")

    output_dir = settings["output_dir"] or os.path.dirname(settings["path"])
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(settings["path"]))[0]
    output_path = os.path.join(output_dir, base + ".gif")

    signals.status.emit(f"Encoding {len(frames)} frames…")
    signals.progress.emit(0.35)

    # GIF can't hold RGBA the way WebP can - it gets one palette and a single
    # fully-transparent colour - so each frame is quantised here rather than
    # letting Pillow flatten it.
    keep_transparency = settings.get("transparency", True) and frames_have_transparency(frames)
    if keep_transparency:
        signals.status.emit("Keeping transparent background…")

    gif_frames = []
    transparent_index = None
    for index, frame in enumerate(frames):
        quantized, transparent_index = quantize_frame(frame, keep_transparency)
        gif_frames.append(quantized)
        signals.progress.emit(0.35 + 0.45 * ((index + 1) / len(frames)))

    signals.status.emit("Writing GIF…")
    save_options = {
        "save_all": True,
        "append_images": gif_frames[1:],
        "duration": durations,
        "loop": 0,
        # Optimising rewrites the palette, which can move or drop the
        # transparent entry, so it's only used on opaque output.
        "optimize": settings.get("optimize", True) and not keep_transparency,
    }
    if transparent_index is not None:
        save_options["transparency"] = transparent_index
        # Clear each frame back to transparent instead of leaving the
        # previous one showing through.
        save_options["disposal"] = 2
    gif_frames[0].save(output_path, **save_options)

    message = (
        f"GIF created successfully.\n\n{output_path}\n\n"
        f"Transparent background: {'kept' if keep_transparency else 'no'}\n"
        f"Frames: {len(frames)}\n"
        f"Size: {frames[0].width} × {frames[0].height}\n"
        f"File size: {_format_file_size(os.path.getsize(output_path))}"
    )

    if settings["export_frames"]:
        signals.status.emit("Exporting PNG frames…")
        folder = _flipbook_save_png_frames(
            frames, output_dir, base, signals, start=0.8, span=0.2
        )
        message += f"\n\nPNG frames: {folder}"

    signals.progress.emit(1.0)
    signals.status.emit(f"Done — {os.path.basename(output_path)}")
    return "Conversion complete", message


def flipbook_png_job(settings, signals):
    signals.status.emit("Extracting frames…")
    frames, _durations, _kind = flipbook_extract_frames(
        settings, report=signals.status.emit
    )
    frames = flipbook_resize_frames(frames, settings["resize"])

    if not frames:
        raise ValueError("No frames were found.")

    output_dir = settings["output_dir"] or os.path.dirname(settings["path"])
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(settings["path"]))[0]

    signals.status.emit(f"Writing {len(frames)} PNG frames…")
    folder = _flipbook_save_png_frames(frames, output_dir, base, signals)

    signals.progress.emit(1.0)
    signals.status.emit(f"PNG frames exported — {os.path.basename(folder)}")
    return "PNG export complete", f"Exported {len(frames)} PNG frame(s) to:\n\n{folder}"


class FlipbookSignals(QObject):
    progress = pyqtSignal(float)
    status = pyqtSignal(str)
    finished = pyqtSignal(str, str)  # title, message
    failed = pyqtSignal(str)


class FlipbookWorker(QRunnable):
    def __init__(self, job, settings):
        super().__init__()
        self.job = job
        self.settings = settings
        self.signals = FlipbookSignals()

    def run(self):
        try:
            title, message = self.job(self.settings, self.signals)
            self.signals.finished.emit(title, message)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


class WebpFlipbookTab(QWidget):
    """Animated WebP and flipbook/sprite-sheet WebP -> animated GIF."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.input_path = ""
        self.output_dir = ""
        self.source_image = None
        self.is_animated = False
        self.preview_frames = []
        self.preview_durations = []
        self.preview_index = 0
        self.busy = False

        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._advance_preview)

        self._build_ui()
        self._update_controls()

    # -- UI -----------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        # Source row
        source_row = QHBoxLayout()
        source_row.setSpacing(12)
        choose_button = QPushButton("Select WebP")
        choose_button.setObjectName("ConvertButton")
        choose_button.setCursor(Qt.PointingHandCursor)
        choose_button.clicked.connect(self.choose_file)
        source_row.addWidget(choose_button)

        self.file_label = QLabel("No file selected")
        self.file_label.setObjectName("MutedLabel")
        source_row.addWidget(self.file_label, 1)
        root.addLayout(source_row)

        columns = QHBoxLayout()
        columns.setSpacing(18)

        # ---- left: controls -------------------------------------------------
        controls_box = QGroupBox("Flipbook / sprite sheet")
        controls = QVBoxLayout(controls_box)
        controls.setContentsMargins(18, 16, 18, 18)
        controls.setSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel("Columns"), 0, 0)
        self.cols_edit = QLineEdit("Auto")
        self.cols_edit.setFixedWidth(90)
        self.cols_edit.setToolTip("Number of frames across. 'Auto' lets the detector decide.")
        self.cols_edit.editingFinished.connect(self._refresh_preview)
        grid.addWidget(self.cols_edit, 0, 1)

        grid.addWidget(QLabel("Rows"), 1, 0)
        self.rows_edit = QLineEdit("Auto")
        self.rows_edit.setFixedWidth(90)
        self.rows_edit.setToolTip("Number of frames down. 'Auto' lets the detector decide.")
        self.rows_edit.editingFinished.connect(self._refresh_preview)
        grid.addWidget(self.rows_edit, 1, 1)

        self.detect_button = QPushButton("Auto Detect Grid")
        self.detect_button.setCursor(Qt.PointingHandCursor)
        self.detect_button.clicked.connect(self.auto_detect_grid)
        grid.addWidget(self.detect_button, 0, 2, 2, 1)
        grid.setColumnStretch(3, 1)
        controls.addLayout(grid)

        hint = QLabel(
            "Leave both on Auto for a normal animated WebP. For a flipbook or "
            "sprite sheet, Auto looks for the repeating frame grid — you can "
            "always type the real numbers in yourself."
        )
        hint.setWordWrap(True)
        hint.setObjectName("HintLabel")
        controls.addWidget(hint)

        settings_grid = QGridLayout()
        settings_grid.setHorizontalSpacing(12)
        settings_grid.setVerticalSpacing(10)

        settings_grid.addWidget(QLabel("Frame order"), 0, 0)
        self.order_combo = QComboBox()
        self.order_combo.addItems(["Row → Column", "Column → Row"])
        self.order_combo.currentIndexChanged.connect(self._refresh_preview)
        settings_grid.addWidget(self.order_combo, 0, 1, 1, 2)

        settings_grid.addWidget(QLabel("GIF FPS"), 1, 0)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(FLIPBOOK_DEFAULT_FPS)
        self.fps_spin.setSuffix(" fps")
        self.fps_spin.setFixedWidth(110)
        settings_grid.addWidget(self.fps_spin, 1, 1)
        settings_grid.setColumnStretch(3, 1)
        controls.addLayout(settings_grid)

        fps_hint = QLabel(
            "FPS is used for sprite sheets. An animated WebP keeps its own "
            "frame timings."
        )
        fps_hint.setWordWrap(True)
        fps_hint.setObjectName("HintLabel")
        controls.addWidget(fps_hint)

        resize_row = QHBoxLayout()
        resize_row.setSpacing(10)
        self.resize_check = QCheckBox("Resize output")
        self.resize_check.toggled.connect(self._toggle_resize)
        resize_row.addWidget(self.resize_check)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 10000)
        self.width_spin.setValue(512)
        self.width_spin.setPrefix("W ")
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 10000)
        self.height_spin.setValue(512)
        self.height_spin.setPrefix("H ")
        resize_row.addWidget(self.width_spin)
        resize_row.addWidget(self.height_spin)
        resize_row.addStretch()
        controls.addLayout(resize_row)

        self.export_frames_check = QCheckBox("Also export individual PNG frames")
        controls.addWidget(self.export_frames_check)

        self.transparency_check = QCheckBox("Keep transparent background")
        self.transparency_check.setChecked(True)
        self.transparency_check.setToolTip(
            "If the source has see-through areas, keep them see-through in the\n"
            "GIF instead of filling them in. GIF transparency is all-or-nothing,\n"
            "so soft edges become either solid or fully clear."
        )
        controls.addWidget(self.transparency_check)

        self.optimize_check = QCheckBox("Optimise GIF where possible")
        self.optimize_check.setChecked(True)
        self.optimize_check.setToolTip(
            "Skipped automatically when a transparent background is being kept."
        )
        controls.addWidget(self.optimize_check)

        output_row = QHBoxLayout()
        output_row.setSpacing(12)
        output_row.addWidget(QLabel("Output folder"))
        self.output_label = QLabel("Same folder as the source")
        self.output_label.setObjectName("MutedLabel")
        output_row.addWidget(self.output_label, 1)
        browse_button = QPushButton("Browse")
        browse_button.setCursor(Qt.PointingHandCursor)
        browse_button.clicked.connect(self.choose_output_folder)
        output_row.addWidget(browse_button)
        controls.addLayout(output_row)

        controls.addStretch()

        self.convert_button = QPushButton("Convert to GIF")
        self.convert_button.setObjectName("ConvertButton")
        self.convert_button.setCursor(Qt.PointingHandCursor)
        self.convert_button.setMinimumHeight(40)
        self.convert_button.clicked.connect(self.start_gif_conversion)
        controls.addWidget(self.convert_button)

        self.png_button = QPushButton("Export PNG frame(s)")
        self.png_button.setCursor(Qt.PointingHandCursor)
        self.png_button.clicked.connect(self.start_png_export)
        controls.addWidget(self.png_button)

        self.progress = QProgressBar()
        self.progress.setObjectName("NiceProgressBar")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        controls.addWidget(self.progress)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("MutedLabel")
        controls.addWidget(self.status_label)

        controls_box.setMinimumWidth(420)
        controls_box.setMaximumWidth(500)
        columns.addWidget(controls_box)

        # ---- right: preview -------------------------------------------------
        preview_box = QGroupBox("Source preview")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(18, 16, 18, 18)
        preview_layout.setSpacing(12)

        self.preview_label = QLabel("Select a WebP to preview it here.")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(300)
        self.preview_label.setObjectName("PreviewBox")
        preview_layout.addWidget(self.preview_label, 1)

        self.grid_overlay_check = QCheckBox("Show detected grid over the sheet")
        self.grid_overlay_check.setChecked(True)
        self.grid_overlay_check.toggled.connect(self._show_preview)
        preview_layout.addWidget(self.grid_overlay_check)

        self.info_label = QLabel("No source loaded.")
        self.info_label.setWordWrap(True)
        self.info_label.setObjectName("MutedLabel")
        preview_layout.addWidget(self.info_label)

        frame_row = QHBoxLayout()
        frame_row.setSpacing(10)
        self.prev_button = QPushButton("◀")
        self.prev_button.setFixedWidth(46)
        self.prev_button.setCursor(Qt.PointingHandCursor)
        self.prev_button.clicked.connect(lambda: self._step_preview(-1))
        frame_row.addWidget(self.prev_button)

        self.play_button = QPushButton("▶ Play")
        self.play_button.setCursor(Qt.PointingHandCursor)
        self.play_button.clicked.connect(self._toggle_play)
        frame_row.addWidget(self.play_button)

        self.frame_label = QLabel("Frame 0 / 0")
        self.frame_label.setAlignment(Qt.AlignCenter)
        frame_row.addWidget(self.frame_label, 1)

        self.next_button = QPushButton("▶")
        self.next_button.setFixedWidth(46)
        self.next_button.setCursor(Qt.PointingHandCursor)
        self.next_button.clicked.connect(lambda: self._step_preview(1))
        frame_row.addWidget(self.next_button)
        preview_layout.addLayout(frame_row)

        columns.addWidget(preview_box, 1)
        root.addLayout(columns, 1)

        self._toggle_resize(False)

    # -- file selection -----------------------------------------------------

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select WebP", "",
            "WebP files (*.webp);;"
            "Image files (*.webp *.png *.gif *.jpg *.jpeg *.bmp *.tif *.tiff);;"
            "All files (*)",
        )
        if not path:
            return

        self.input_path = path
        self.output_dir = os.path.dirname(path)
        self.file_label.setText(path)
        self.output_label.setText(self.output_dir)

        try:
            self.load_source()
        except Exception as exc:  # noqa: BLE001
            self.source_image = None
            self.preview_frames = []
            self._update_controls()
            show_error(self, "Could not open file", str(exc))
            self.status_label.setText("Could not load source.")

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_dir)
        if folder:
            self.output_dir = folder
            self.output_label.setText(folder)

    def load_source(self):
        self._stop_play()
        self.source_image = Image.open(self.input_path)
        self.is_animated = getattr(self.source_image, "n_frames", 1) > 1

        self.preview_frames = []
        self.preview_durations = []
        if self.is_animated:
            for index, frame in enumerate(ImageSequence.Iterator(self.source_image)):
                if index >= FLIPBOOK_MAX_PREVIEW_FRAMES:
                    break
                self.preview_frames.append(frame.convert("RGBA").copy())
                self.preview_durations.append(
                    max(20, int(frame.info.get("duration", 100) or 100))
                )
        else:
            self.preview_frames = [self.source_image.convert("RGBA").copy()]
            self.preview_durations = [100]

        self.preview_index = 0

        native_frames = getattr(self.source_image, "n_frames", 1)
        kind = "Animated WebP" if self.is_animated else "Static image / sprite sheet"
        self.info_label.setText(
            f"Type: {kind}\n"
            f"Canvas: {self.source_image.width} × {self.source_image.height}\n"
            f"Native frames: {native_frames}\n"
            f"File size: {_format_file_size(os.path.getsize(self.input_path))}"
        )

        if self.is_animated:
            self.cols_edit.setText("Auto")
            self.rows_edit.setText("Auto")
            self.status_label.setText(
                "Animated WebP loaded — frame timings will be preserved."
            )
        else:
            self.auto_detect_grid(quiet=True)
            self.status_label.setText("Source loaded.")

        self._update_controls()
        self._show_preview()

    # -- grid ---------------------------------------------------------------

    def _parse_grid(self):
        """Returns (cols, rows) with None meaning 'work it out automatically'."""
        def parse(text, name):
            text = text.strip().lower()
            if text in ("", "auto"):
                return None
            try:
                value = int(text)
            except ValueError:
                raise ValueError(f"{name} must be a whole number, or 'Auto'.")
            if value < 1:
                raise ValueError(f"{name} must be at least 1.")
            return value

        return parse(self.cols_edit.text(), "Columns"), parse(self.rows_edit.text(), "Rows")

    def auto_detect_grid(self, quiet=False):
        if self.source_image is None:
            return
        try:
            columns, rows, reason = detect_flipbook_grid(self.source_image, self.is_animated)
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                show_error(self, "Auto detection failed", str(exc))
            return

        self.cols_edit.setText(str(columns))
        self.rows_edit.setText(str(rows))
        self.status_label.setText(f"Detected {columns} × {rows} grid — {reason}.")
        self._show_preview()

    def _refresh_preview(self):
        self._show_preview()

    # -- preview ------------------------------------------------------------

    def _current_grid_for_preview(self):
        """Grid to draw over a sprite sheet, or None if it doesn't apply."""
        if self.is_animated or self.source_image is None:
            return None
        try:
            columns, rows = self._parse_grid()
        except ValueError:
            return None
        if not columns or not rows or (columns == 1 and rows == 1):
            return None
        return columns, rows

    def _show_preview(self):
        if not self.preview_frames:
            self.preview_label.setText("Select a WebP to preview it here.")
            self.frame_label.setText("Frame 0 / 0")
            return

        frame = self.preview_frames[self.preview_index]
        box_width = max(240, self.preview_label.width() - 24)
        box_height = max(200, self.preview_label.height() - 24)

        pixmap = QPixmap.fromImage(pil_to_qimage(frame)).scaled(
            box_width, box_height, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

        grid = self._current_grid_for_preview()
        if grid and self.grid_overlay_check.isChecked():
            pixmap = self._draw_grid(pixmap, *grid)

        self.preview_label.setPixmap(pixmap)
        self.frame_label.setText(
            f"Frame {self.preview_index + 1} / {len(self.preview_frames)}"
        )

    @staticmethod
    def _draw_grid(pixmap, columns, rows):
        """Draws the detected cell boundaries over the sheet so it's obvious
        whether the detected grid actually lines up with the artwork."""
        overlaid = QPixmap(pixmap)
        painter = QPainter(overlaid)
        pen = QPen(QColor(FLIPBOOK_GRID_OVERLAY_COLOR))
        pen.setWidth(1)
        painter.setPen(pen)
        width, height = overlaid.width(), overlaid.height()
        for column in range(1, columns):
            x = round(width * column / columns)
            painter.drawLine(x, 0, x, height)
        for row in range(1, rows):
            y = round(height * row / rows)
            painter.drawLine(0, y, width, y)
        painter.end()
        return overlaid

    def _step_preview(self, delta):
        if not self.preview_frames:
            return
        self._stop_play()
        self.preview_index = (self.preview_index + delta) % len(self.preview_frames)
        self._show_preview()

    def _advance_preview(self):
        if not self.preview_frames:
            return
        self.preview_index = (self.preview_index + 1) % len(self.preview_frames)
        self._show_preview()
        self.play_timer.start(self.preview_durations[self.preview_index])

    def _toggle_play(self):
        if self.play_timer.isActive():
            self._stop_play()
        elif len(self.preview_frames) > 1:
            self.play_button.setText("⏸ Pause")
            self.play_timer.start(self.preview_durations[self.preview_index])

    def _stop_play(self):
        self.play_timer.stop()
        self.play_button.setText("▶ Play")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.preview_frames:
            self._show_preview()

    # -- conversion ---------------------------------------------------------

    def _toggle_resize(self, checked):
        self.width_spin.setEnabled(checked)
        self.height_spin.setEnabled(checked)

    def _update_controls(self):
        has_source = self.source_image is not None
        sprite = has_source and not self.is_animated
        for widget in (self.convert_button, self.png_button):
            widget.setEnabled(has_source and not self.busy)
        for widget in (self.cols_edit, self.rows_edit, self.detect_button,
                       self.order_combo, self.fps_spin, self.grid_overlay_check):
            widget.setEnabled(sprite)
        playable = has_source and len(self.preview_frames) > 1
        for widget in (self.prev_button, self.next_button, self.play_button):
            widget.setEnabled(playable)

    def _gather_settings(self):
        """Everything the worker needs, read on the GUI thread."""
        columns, rows = self._parse_grid()
        resize = None
        if self.resize_check.isChecked():
            resize = (self.width_spin.value(), self.height_spin.value())
        return {
            "path": self.input_path,
            "cols": columns,
            "rows": rows,
            "order": "row" if self.order_combo.currentIndex() == 0 else "column",
            "fps": float(self.fps_spin.value()),
            "resize": resize,
            "export_frames": self.export_frames_check.isChecked(),
            "optimize": self.optimize_check.isChecked(),
            "transparency": self.transparency_check.isChecked(),
            "output_dir": self.output_dir,
        }

    def _start_job(self, job):
        if not self.input_path or self.source_image is None:
            show_error(self, "No source", "Select a WebP file first.")
            return
        if self.busy:
            return
        try:
            settings = self._gather_settings()
        except ValueError as exc:
            show_error(self, "Check the settings", str(exc))
            return

        self._stop_play()
        self.busy = True
        self._update_controls()
        self.progress.setValue(0)
        self.status_label.setText("Working…")

        worker = FlipbookWorker(job, settings)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.status.connect(self.status_label.setText)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)
        self.main_window.thread_pool.start(worker)

    def start_gif_conversion(self):
        self._start_job(flipbook_gif_job)

    def start_png_export(self):
        self._start_job(flipbook_png_job)

    def _on_progress(self, value):
        self.progress.setValue(int(max(0.0, min(1.0, value)) * 100))

    def _on_finished(self, title, message):
        self.busy = False
        self._update_controls()
        self.progress.setValue(100)
        QMessageBox.information(self, title, message)

    def _on_failed(self, error_message):
        self.busy = False
        self._update_controls()
        self.progress.setValue(0)
        self.status_label.setText("Failed.")
        show_critical(self, "Conversion failed", error_message)


# ---------------------------------------------------------------------------
# Spacing
#
# The original tool tabs were laid out with Qt's fairly tight defaults. Rather
# than hand-editing dozens of individual layouts, apply_comfortable_spacing()
# walks a tab once after it's built and raises every gap to at least these
# minimums - anything already roomier is left exactly as it was.
# ---------------------------------------------------------------------------

PAGE_MARGINS = (22, 14, 22, 14)       # around the edge of each tab
GROUP_MARGINS = (16, 16, 16, 16)      # inside each titled panel
MIN_ROW_SPACING = 12                  # between stacked / side-by-side items
MIN_GRID_SPACING = (14, 16)           # horizontal, vertical in grids


def _at_least(current, minimum):
    return max(current if current is not None and current >= 0 else 0, minimum)


def apply_comfortable_spacing(page):
    top_layout = page.layout()
    for layout in [top_layout] + page.findChildren(QLayout):
        if layout is None:
            continue
        if isinstance(layout, QGridLayout):
            layout.setHorizontalSpacing(_at_least(layout.horizontalSpacing(), MIN_GRID_SPACING[0]))
            layout.setVerticalSpacing(_at_least(layout.verticalSpacing(), MIN_GRID_SPACING[1]))
        elif isinstance(layout, QBoxLayout):
            layout.setSpacing(_at_least(layout.spacing(), MIN_ROW_SPACING))
        else:
            continue  # e.g. QToolBar's private layout

        owner = layout.parentWidget()
        if isinstance(owner, QGroupBox) and owner.layout() is layout:
            margins = layout.contentsMargins()
            layout.setContentsMargins(
                _at_least(margins.left(), GROUP_MARGINS[0]),
                _at_least(margins.top(), GROUP_MARGINS[1]),
                _at_least(margins.right(), GROUP_MARGINS[2]),
                _at_least(margins.bottom(), GROUP_MARGINS[3]),
            )

    if top_layout is not None:
        margins = top_layout.contentsMargins()
        top_layout.setContentsMargins(
            _at_least(margins.left(), PAGE_MARGINS[0]),
            _at_least(margins.top(), PAGE_MARGINS[1]),
            _at_least(margins.right(), PAGE_MARGINS[2]),
            _at_least(margins.bottom(), PAGE_MARGINS[3]),
        )


class _MinimumHintHolder(QWidget):
    """Sits between a tab and its scroll area.

    A resizable QScrollArea grows its content to the content's *preferred*
    size (and, when there's word-wrapped text anywhere inside, to a
    height-for-width figure that's also based on preferred sizes). List
    widgets, browser views and the like prefer far more room than they need,
    so tabs scrolled even when everything would fit.

    This holder deliberately has no layout of its own: it reports the tab's
    true minimum as both its minimum and its preference, and stretches the tab
    over whatever space it's given. So a tab only scrolls when the window
    genuinely can't fit it, and otherwise just fills the window."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        page.setParent(self)

    def minimumSizeHint(self):
        return self.page.minimumSizeHint().expandedTo(self.page.minimumSize())

    def sizeHint(self):
        return self.minimumSizeHint()

    def resizeEvent(self, event):
        self.page.setGeometry(self.rect())
        super().resizeEvent(event)

    def event(self, event):
        # The tab's contents changed size requirements - pass that up so the
        # scroll area re-checks whether it needs to scroll.
        if event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().event(event)


def wrap_in_scroll(page):
    """Puts a tab inside a scroll area. When the window is big enough this is
    invisible; when it isn't, the tab scrolls instead of Qt squashing its
    contents on top of each other."""
    holder = _MinimumHintHolder(page)

    scroll = QScrollArea()
    scroll.setObjectName("TabScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(holder)
    return scroll


# ---------------------------------------------------------------------------
# Background Remover tab
#
# Point-and-click background removal: click a background area to clear it,
# paint out whatever the automatic pass misses, then save as PNG/WebP/AVIF
# with real transparency.
#
# Everything here works on the image's alpha channel. The colour data is never
# touched, so nothing is destroyed until the file is saved - and Undo just
# restores a previous alpha channel.
# ---------------------------------------------------------------------------

CUTOUT_MAX_UNDO = 20
CUTOUT_DEFAULT_TOLERANCE = 40
CUTOUT_DEFAULT_BRUSH = 40

# ---------------------------------------------------------------------------
# AI subject detection
#
# U^2-Net looks at the whole picture and works out what the subject is, which
# is what you want for a person or character against a background the colour
# tools can't separate. It runs locally through onnxruntime; the model file
# is downloaded once, on first use, and cached.
# ---------------------------------------------------------------------------

try:
    import onnxruntime
    ONNX_AVAILABLE = True
except ImportError:
    onnxruntime = None
    ONNX_AVAILABLE = False

AI_MODEL_RELEASE = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"

# label -> (file name, approximate download size, what it's good at)
AI_MODELS = {
    "Balanced — general subjects (5 MB)": ("u2netp.onnx", 5),
    "Best quality — general subjects (176 MB)": ("u2net.onnx", 176),
    "People / characters (176 MB)": ("u2net_human_seg.onnx", 176),
}
AI_INPUT_SIZE = 320
_AI_SESSIONS = {}


def ai_model_folder():
    """Where downloaded models live - beside the app's other settings, so a
    rebuilt .exe doesn't mean downloading them again."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "ImageGen", "models")
    os.makedirs(folder, exist_ok=True)
    return folder


def ai_model_path(file_name):
    return os.path.join(ai_model_folder(), file_name)


def download_ai_model(file_name, progress=None):
    """Fetches a model to the cache folder. `progress` takes a 0-1 fraction."""
    destination = ai_model_path(file_name)
    if os.path.exists(destination) and os.path.getsize(destination) > 1024:
        return destination

    partial = destination + ".part"
    request = urllib.request.Request(
        AI_MODEL_RELEASE + file_name, headers={"User-Agent": APP_TITLE}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        with open(partial, "wb") as handle:
            while True:
                chunk = response.read(262144)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress and total:
                    progress(downloaded / total)
    os.replace(partial, destination)
    return destination


def ai_session(file_name):
    """Loads (and caches) an onnxruntime session for a model file."""
    if file_name not in _AI_SESSIONS:
        _AI_SESSIONS[file_name] = onnxruntime.InferenceSession(
            ai_model_path(file_name), providers=["CPUExecutionProvider"]
        )
    return _AI_SESSIONS[file_name]


def ai_subject_alpha(image, file_name):
    """Returns an alpha channel ("L", same size as `image`) where the subject
    is opaque and everything else is see-through."""
    session = ai_session(file_name)
    rgb = image.convert("RGB")
    small = rgb.resize((AI_INPUT_SIZE, AI_INPUT_SIZE), RESAMPLE_LANCZOS)

    sample = np.asarray(small, dtype=np.float32) / 255.0
    sample = (sample - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / \
        np.array([0.229, 0.224, 0.225], dtype=np.float32)
    sample = sample.transpose(2, 0, 1)[np.newaxis]

    prediction = session.run(None, {session.get_inputs()[0].name: sample})[0][0, 0]
    spread = prediction.max() - prediction.min()
    prediction = (prediction - prediction.min()) / (spread if spread else 1.0)

    mask = Image.fromarray((prediction * 255).astype(np.uint8), mode="L")
    return mask.resize(rgb.size, RESAMPLE_LANCZOS)


def _alpha_array(alpha_image):
    return np.array(alpha_image, dtype=np.uint8)


def colour_match_mask(rgb_array, colour, tolerance):
    """Pixels within `tolerance` of `colour`, per channel."""
    difference = np.abs(rgb_array.astype(np.int16) - np.asarray(colour, dtype=np.int16))
    return difference.max(axis=2) <= tolerance


def flood_region(similar, seed_x, seed_y):
    """Connected run of True pixels reachable from the seed.

    Scanline fill: whole horizontal spans are filled at once and only the
    starts of new runs get pushed, which keeps even large images quick.
    """
    height, width = similar.shape
    filled = np.zeros_like(similar)
    if not similar[seed_y, seed_x]:
        return filled

    stack = [(seed_x, seed_y)]
    while stack:
        x, y = stack.pop()
        if filled[y, x] or not similar[y, x]:
            continue

        left = x
        while left > 0 and similar[y, left - 1] and not filled[y, left - 1]:
            left -= 1
        right = x
        while right < width - 1 and similar[y, right + 1] and not filled[y, right + 1]:
            right += 1
        filled[y, left:right + 1] = True

        for neighbour_y in (y - 1, y + 1):
            if not 0 <= neighbour_y < height:
                continue
            row = similar[neighbour_y, left:right + 1] & ~filled[neighbour_y, left:right + 1]
            indices = np.flatnonzero(row)
            if indices.size:
                run_starts = indices[np.r_[True, np.diff(indices) > 1]]
                for start in run_starts:
                    stack.append((left + int(start), neighbour_y))
    return filled


class CutoutCanvas(QWidget):
    """Shows the cutout over a transparency checkerboard, reports clicks and
    drags back in image coordinates, and handles zooming and panning.

    Zoom is purely a way of looking at the picture - it never changes the
    image itself, so what gets saved is the same whatever the zoom is.
    """

    pressed_at = pyqtSignal(int, int)
    dragged_to = pyqtSignal(int, int)
    released = pyqtSignal()
    view_changed = pyqtSignal()

    MIN_ZOOM = 1.0
    MAX_ZOOM = 30.0
    ZOOM_STEP = 1.25
    PAN_STEP = 0.2  # of the visible area, per button press

    def __init__(self):
        super().__init__()
        self.setMinimumSize(380, 340)
        self.setCursor(Qt.CrossCursor)
        self._pixmap = None
        self._target = QRect()
        self._zoom = 1.0
        # The image point shown at the middle of the widget, in image pixels.
        self._centre = QPointF(0, 0)
        self._panning_from = None

    # -- view state ----------------------------------------------------------

    @property
    def zoom(self):
        return self._zoom

    def is_zoomed(self):
        return abs(self._zoom - 1.0) > 0.001

    def set_pixmap(self, pixmap, keep_view=False):
        first = self._pixmap is None
        self._pixmap = pixmap
        if pixmap is not None and (first or not keep_view):
            self._centre = QPointF(pixmap.width() / 2, pixmap.height() / 2)
        if not keep_view:
            self._zoom = 1.0
        self._clamp_centre()
        self.update()
        self.view_changed.emit()

    def reset_view(self):
        self._zoom = 1.0
        if self._pixmap is not None:
            self._centre = QPointF(self._pixmap.width() / 2, self._pixmap.height() / 2)
        self.update()
        self.view_changed.emit()

    def _fit_scale(self):
        """Widget pixels per image pixel with the whole image on screen."""
        if self._pixmap is None or self._pixmap.isNull():
            return 1.0
        area = self.rect().adjusted(8, 8, -8, -8)
        return min(area.width() / self._pixmap.width(),
                   area.height() / self._pixmap.height())

    def _scale(self):
        return self._fit_scale() * self._zoom

    def _clamp_centre(self):
        """Keeps the view over the image, and centres it on whichever axis is
        smaller than the viewport."""
        if self._pixmap is None or self._pixmap.isNull():
            return
        scale = self._scale()
        half_width = self.width() / (2 * scale)
        half_height = self.height() / (2 * scale)
        width, height = self._pixmap.width(), self._pixmap.height()

        if half_width * 2 >= width:
            x = width / 2
        else:
            x = min(max(self._centre.x(), half_width), width - half_width)
        if half_height * 2 >= height:
            y = height / 2
        else:
            y = min(max(self._centre.y(), half_height), height - half_height)
        self._centre = QPointF(x, y)

    def zoom_by(self, factor, focus=None):
        """Zooms about `focus` (a widget point) - or the middle if not given -
        so whatever is under the cursor stays under the cursor."""
        if self._pixmap is None or self._pixmap.isNull():
            return
        new_zoom = min(max(self._zoom * factor, self.MIN_ZOOM), self.MAX_ZOOM)
        if abs(new_zoom - self._zoom) < 1e-6:
            return

        if focus is not None:
            anchor = self._to_image_point(focus)
            self._zoom = new_zoom
            new_scale = self._scale()
            widget_centre = QPointF(self.width() / 2, self.height() / 2)
            offset = QPointF(focus.x() - widget_centre.x(), focus.y() - widget_centre.y())
            self._centre = QPointF(anchor.x() - offset.x() / new_scale,
                                   anchor.y() - offset.y() / new_scale)
        else:
            self._zoom = new_zoom

        self._clamp_centre()
        self.update()
        self.view_changed.emit()

    def pan_by(self, dx, dy):
        """Moves the view by a fraction of what's on screen."""
        if self._pixmap is None or self._pixmap.isNull():
            return
        scale = self._scale()
        self._centre = QPointF(
            self._centre.x() + dx * self.width() * self.PAN_STEP / scale,
            self._centre.y() + dy * self.height() * self.PAN_STEP / scale,
        )
        self._clamp_centre()
        self.update()
        self.view_changed.emit()

    # -- painting --------------------------------------------------------------

    def _image_rect(self):
        if self._pixmap is None or self._pixmap.isNull():
            return QRect()
        scale = self._scale()
        width = self._pixmap.width() * scale
        height = self._pixmap.height() * scale
        left = self.width() / 2 - self._centre.x() * scale
        top = self.height() / 2 - self._centre.y() * scale
        return QRect(round(left), round(top), round(width), round(height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._clamp_centre()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(WEB_COLORS["BG"]))
        if self._pixmap is None or self._pixmap.isNull():
            painter.setPen(QColor(WEB_COLORS["MUTED"]))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Open an image to start removing its background")
            painter.end()
            return

        self._target = self._image_rect()
        visible = self._target.intersected(self.rect())
        painter.fillRect(visible, checker_brush())
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self._zoom <= 4)
        painter.drawPixmap(self._target, self._pixmap)
        painter.setPen(QColor(WEB_COLORS["BORDER"]))
        painter.drawRect(self._target.adjusted(0, 0, -1, -1))
        painter.end()

    # -- coordinates -----------------------------------------------------------

    def _to_image_point(self, position):
        scale = self._scale()
        widget_centre = QPointF(self.width() / 2, self.height() / 2)
        return QPointF(
            self._centre.x() + (position.x() - widget_centre.x()) / scale,
            self._centre.y() + (position.y() - widget_centre.y()) / scale,
        )

    def _to_image(self, position):
        if self._pixmap is None or self._pixmap.isNull():
            return None
        point = self._to_image_point(position)
        x, y = int(point.x()), int(point.y())
        if not (0 <= x < self._pixmap.width() and 0 <= y < self._pixmap.height()):
            return None
        return x, y

    # -- input -----------------------------------------------------------------

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(self.ZOOM_STEP ** steps, event.position())
            event.accept()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            # Drag with the middle or right button to shove the image around.
            self._panning_from = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        point = self._to_image(event.position().toPoint())
        if point is not None:
            self.pressed_at.emit(*point)

    def mouseMoveEvent(self, event):
        if self._panning_from is not None:
            scale = self._scale()
            delta = event.position() - self._panning_from
            self._panning_from = event.position()
            self._centre = QPointF(self._centre.x() - delta.x() / scale,
                                   self._centre.y() - delta.y() / scale)
            self._clamp_centre()
            self.update()
            self.view_changed.emit()
            return
        if event.buttons() & Qt.LeftButton:
            point = self._to_image(event.position().toPoint())
            if point is not None:
                self.dragged_to.emit(*point)

    def mouseReleaseEvent(self, event):
        if self._panning_from is not None:
            self._panning_from = None
            self.setCursor(Qt.CrossCursor)
            return
        self.released.emit()


class CutoutSignals(QObject):
    finished = pyqtSignal(object, str)   # alpha image, message
    failed = pyqtSignal(str)
    status = pyqtSignal(str)
    progress = pyqtSignal(float)         # download progress, 0-1


class CutoutWorker(QRunnable):
    """Runs the AI cutout off the GUI thread - downloading the model if this
    is the first time it's been used, then running the model itself."""

    def __init__(self, image, model_file):
        super().__init__()
        self.image = image
        self.model_file = model_file
        self.signals = CutoutSignals()

    def run(self):
        try:
            if not os.path.exists(ai_model_path(self.model_file)):
                self.signals.status.emit("Downloading the AI model (one time only)…")
                download_ai_model(self.model_file, self.signals.progress.emit)
            self.signals.progress.emit(1.0)
            self.signals.status.emit("Looking at the image…")
            alpha = ai_subject_alpha(self.image, self.model_file)
            self.signals.finished.emit(alpha, "AI found the subject.")
        except urllib.error.URLError as exc:
            self.signals.failed.emit(
                "Couldn't download the AI model - check the internet "
                f"connection and try again.\n\n{exc}"
            )
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


class BackgroundRemoverTab(QWidget):
    """Remove an image's background and save it with transparency."""

    TOOL_WAND = "wand"
    TOOL_ERASE = "erase"
    TOOL_RESTORE = "restore"

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.source = None          # RGBA, colours never modified
        self.rgb_array = None       # numpy view of the colours, for matching
        self.alpha = None           # current transparency, PIL "L"
        self.source_path = ""
        self.history = []
        self.tool = self.TOOL_WAND
        self.busy = False
        self._last_brush_point = None
        self._reset_view_next = True
        self._build_ui()
        self._update_controls()

    # -- UI ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        open_button = QPushButton("Open Image")
        open_button.setObjectName("ConvertButton")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.clicked.connect(self.choose_image)
        top_row.addWidget(open_button)
        self.file_label = QLabel("No image selected")
        self.file_label.setObjectName("MutedLabel")
        top_row.addWidget(self.file_label, 1)
        root.addLayout(top_row)

        columns = QHBoxLayout()
        columns.setSpacing(18)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(14)
        panel_layout.addWidget(self._build_quick_group())
        panel_layout.addWidget(self._build_tools_group())
        panel_layout.addWidget(self._build_edges_group())
        panel_layout.addWidget(self._build_save_group())
        panel_layout.addStretch()

        panel_scroll = QScrollArea()
        panel_scroll.setObjectName("PanelScroll")
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFrameShape(QFrame.NoFrame)
        panel_scroll.setWidget(panel)
        panel_scroll.setMinimumWidth(300)

        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.setSpacing(10)
        self.canvas = CutoutCanvas()
        self.canvas.pressed_at.connect(self.on_canvas_pressed)
        self.canvas.dragged_to.connect(self.on_canvas_dragged)
        self.canvas.released.connect(self.on_canvas_released)
        self.canvas.view_changed.connect(self._update_zoom_controls)
        preview_layout.addWidget(self.canvas, 1)
        preview_layout.addLayout(self._build_view_controls())
        self.status_label = QLabel(
            "Open an image, then click the background to clear it. "
            "The chequered areas are see-through."
        )
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        preview_layout.addWidget(self.status_label)
        # Draggable divider, so the tools and the picture can be balanced.
        self.splitter = GripSplitter(Qt.Horizontal)
        self.splitter.setObjectName("BrowserSplitter")
        self.splitter.setHandleWidth(14)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(panel_scroll)
        self.splitter.addWidget(preview_box)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        preview_box.setMinimumWidth(320)
        columns.addWidget(self.splitter, 1)
        self._sized_splitter = False

        root.addLayout(columns, 1)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._sized_splitter and self.splitter.width() > 100:
            self._sized_splitter = True
            total = self.splitter.width()
            panel = min(440, max(330, total // 3))
            self.splitter.setSizes([panel, total - panel])

    def _build_view_controls(self):
        """Zoom and pan controls for the preview. Zoom is only a way of
        looking at the image closely while brushing - it never changes the
        picture, and the whole image is always what gets saved."""
        row = QHBoxLayout()
        row.setSpacing(8)

        def small_button(text, tooltip, handler):
            button = QPushButton(text)
            button.setObjectName("IconButton")
            button.setFixedSize(34, 30)
            button.setToolTip(tooltip)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(handler)
            return button

        self.zoom_out_button = small_button(
            "−", "Zoom out", lambda: self.canvas.zoom_by(1 / CutoutCanvas.ZOOM_STEP))
        self.zoom_in_button = small_button(
            "+", "Zoom in", lambda: self.canvas.zoom_by(CutoutCanvas.ZOOM_STEP))
        row.addWidget(self.zoom_out_button)
        row.addWidget(self.zoom_in_button)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("MutedLabel")
        self.zoom_label.setMinimumWidth(52)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        row.addWidget(self.zoom_label)

        self.zoom_reset_button = QPushButton("Fit")
        self.zoom_reset_button.setObjectName("ChipButton")
        self.zoom_reset_button.setToolTip("Back to the whole image")
        self.zoom_reset_button.setCursor(Qt.PointingHandCursor)
        self.zoom_reset_button.clicked.connect(self.canvas.reset_view)
        row.addWidget(self.zoom_reset_button)

        row.addSpacing(12)
        self.pan_buttons = [
            small_button("←", "Move left", lambda: self.canvas.pan_by(-1, 0)),
            small_button("↑", "Move up", lambda: self.canvas.pan_by(0, -1)),
            small_button("↓", "Move down", lambda: self.canvas.pan_by(0, 1)),
            small_button("→", "Move right", lambda: self.canvas.pan_by(1, 0)),
        ]
        for button in self.pan_buttons:
            row.addWidget(button)

        hint = QLabel("Scroll to zoom where the cursor is • drag with the right "
                      "or middle button to move around")
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        row.addWidget(hint, 1)
        return row

    def _update_zoom_controls(self):
        zoom = self.canvas.zoom
        self.zoom_label.setText(f"{zoom * 100:.0f}%")
        loaded = self.source is not None
        self.zoom_in_button.setEnabled(loaded and zoom < CutoutCanvas.MAX_ZOOM - 1e-6)
        self.zoom_out_button.setEnabled(loaded and zoom > CutoutCanvas.MIN_ZOOM + 1e-6)
        self.zoom_reset_button.setEnabled(loaded and self.canvas.is_zoomed())
        for button in self.pan_buttons:
            button.setEnabled(loaded and self.canvas.is_zoomed())

    def _build_quick_group(self):
        box = QGroupBox("Remove the background for me")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.model_combo = QComboBox()
        self.model_combo.addItems(list(AI_MODELS))
        self.model_combo.setToolTip(
            "Which model looks at the image.\n"
            "Balanced is quick and small. Best quality is slower but cleaner. "
            "People / characters is trained on human subjects."
        )
        layout.addWidget(self.model_combo)

        self.ai_button = QPushButton("AI: find the subject and cut it out")
        self.ai_button.setObjectName("ConvertButton")
        self.ai_button.setCursor(Qt.PointingHandCursor)
        self.ai_button.setMinimumHeight(40)
        self.ai_button.clicked.connect(self.run_ai_cutout)
        layout.addWidget(self.ai_button)

        self.ai_progress = QProgressBar()
        self.ai_progress.setObjectName("NiceProgressBar")
        self.ai_progress.setRange(0, 100)
        self.ai_progress.setTextVisible(False)
        self.ai_progress.setFixedHeight(10)
        self.ai_progress.setVisible(False)
        layout.addWidget(self.ai_progress)

        if ONNX_AVAILABLE:
            hint = ("Works out what the subject is by looking at the picture, "
                    "so it copes with busy backgrounds and colours that match "
                    "the subject. The model downloads itself the first time "
                    "you use it, then it's offline from then on.")
        else:
            hint = ("The AI cutout needs onnxruntime, which isn't installed "
                    "in this Python:\n    pip install onnxruntime\n"
                    "Then restart the app. The colour tools below work without it.")
        ai_hint = QLabel(hint)
        ai_hint.setObjectName("HintLabel")
        ai_hint.setWordWrap(True)
        layout.addWidget(ai_hint)

        self.auto_button = QPushButton("Or: clear the background from the edges")
        self.auto_button.setCursor(Qt.PointingHandCursor)
        self.auto_button.setToolTip(
            "Samples the four corners and clears everything connected to them "
            "that matches.\nBest on a plain or flat background."
        )
        self.auto_button.clicked.connect(self.remove_edge_background)
        layout.addWidget(self.auto_button)
        return box

    def _build_tools_group(self):
        box = QGroupBox("Touch up by hand")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        self.tool_buttons = {}
        for label, tool, tip in (
            ("Magic wand", self.TOOL_WAND, "Click a colour in the preview to clear it"),
            ("Erase", self.TOOL_ERASE, "Paint parts away"),
            ("Restore", self.TOOL_RESTORE, "Paint cleared parts back"),
        ):
            button = QPushButton(label)
            button.setObjectName("ChipButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, t=tool: self.set_tool(t))
            tool_row.addWidget(button)
            self.tool_buttons[tool] = button
        self.tool_buttons[self.TOOL_WAND].setChecked(True)
        layout.addLayout(tool_row)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)

        form.addWidget(QLabel("Tolerance"), 0, 0)
        self.tolerance_slider = QSlider(Qt.Horizontal)
        self.tolerance_slider.setRange(0, 150)
        self.tolerance_slider.setValue(CUTOUT_DEFAULT_TOLERANCE)
        self.tolerance_slider.setToolTip(
            "How different a colour can be and still count as background."
        )
        form.addWidget(self.tolerance_slider, 0, 1)
        self.tolerance_label = QLabel(str(CUTOUT_DEFAULT_TOLERANCE))
        self.tolerance_label.setMinimumWidth(38)
        self.tolerance_label.setAlignment(Qt.AlignCenter)
        form.addWidget(self.tolerance_label, 0, 2)
        self.tolerance_slider.valueChanged.connect(
            lambda value: self.tolerance_label.setText(str(value))
        )

        form.addWidget(QLabel("Brush size"), 1, 0)
        self.brush_slider = QSlider(Qt.Horizontal)
        self.brush_slider.setRange(2, 250)
        self.brush_slider.setValue(CUTOUT_DEFAULT_BRUSH)
        form.addWidget(self.brush_slider, 1, 1)
        self.brush_label = QLabel(str(CUTOUT_DEFAULT_BRUSH))
        self.brush_label.setMinimumWidth(38)
        self.brush_label.setAlignment(Qt.AlignCenter)
        form.addWidget(self.brush_label, 1, 2)
        self.brush_slider.valueChanged.connect(
            lambda value: self.brush_label.setText(str(value))
        )
        form.setColumnStretch(1, 1)
        layout.addLayout(form)

        self.contiguous_check = QCheckBox("Only clear the area I click on")
        self.contiguous_check.setChecked(True)
        self.contiguous_check.setToolTip(
            "On: clears the connected patch under the cursor.\n"
            "Off: clears that colour everywhere in the image."
        )
        layout.addWidget(self.contiguous_check)

        undo_row = QHBoxLayout()
        undo_row.setSpacing(10)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setCursor(Qt.PointingHandCursor)
        self.undo_button.clicked.connect(self.undo)
        undo_row.addWidget(self.undo_button)
        self.reset_button = QPushButton("Start over")
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.clicked.connect(self.reset_alpha)
        undo_row.addWidget(self.reset_button)
        layout.addLayout(undo_row)
        return box

    def _build_edges_group(self):
        box = QGroupBox("Tidy up the edges")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(8)
        for label, handler, tip in (
            ("Soften", self.soften_edges, "Blurs the cut edge slightly so it looks less jagged"),
            ("Tighten", self.tighten_edges, "Shaves a pixel off the edge to remove a colour halo"),
            ("Trim", self.trim_to_subject, "Crops away the empty space around what's left"),
        ):
            button = QPushButton(label)
            button.setObjectName("ChipButton")
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        return box

    def _build_save_group(self):
        box = QGroupBox("Save")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["PNG"] + (["WebP"]) + (["AVIF"] if AVIF_AVAILABLE else []))
        row.addWidget(self.format_combo, 1)
        self.save_button = QPushButton("Save Image…")
        self.save_button.setObjectName("ConvertButton")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.clicked.connect(self.save_image)
        row.addWidget(self.save_button, 1)
        layout.addLayout(row)
        note = QLabel(
            "PNG, WebP and AVIF all keep transparency. JPG can't, so it isn't offered."
        )
        note.setObjectName("HintLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        return box

    # -- Loading ---------------------------------------------------------------

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open image", "", WEB_IMAGE_FILTER
        )
        if path:
            self.load_image_from_path(path)

    def load_image_from_path(self, path):
        """Loads an image into the tab. Also used when another tab hands one
        over, such as a freshly generated picture."""
        try:
            image = Image.open(path)
            image.load()
        except Exception as exc:  # noqa: BLE001
            show_critical(self, "Could not open image", str(exc))
            return

        self.source_path = path
        self.source = image.convert("RGBA")
        self.rgb_array = np.array(self.source.convert("RGB"), dtype=np.uint8)
        self.alpha = self.source.getchannel("A")
        self.history = []
        self._reset_view_next = True
        self.file_label.setText(
            f"{os.path.basename(path)}  •  {self.source.width} × {self.source.height}"
        )
        self.set_status("Click the background to clear it, or try one-click removal.")
        self._update_controls()
        self.refresh_preview()

    # -- Alpha helpers ---------------------------------------------------------

    def _push_history(self):
        if self.alpha is None:
            return
        self.history.append(self.alpha.copy())
        del self.history[:-CUTOUT_MAX_UNDO]
        self.undo_button.setEnabled(True)

    def undo(self):
        if not self.history:
            return
        self.alpha = self.history.pop()
        self.undo_button.setEnabled(bool(self.history))
        self.set_status("Undone.")
        self.refresh_preview()

    def reset_alpha(self):
        if self.source is None:
            return
        self._push_history()
        self.alpha = self.source.getchannel("A")
        self.set_status("Back to the original image.")
        self.refresh_preview()

    def current_image(self):
        """The image as it stands: original colours, current transparency."""
        if self.source is None:
            return None
        result = self.source.copy()
        result.putalpha(self.alpha)
        return result

    def refresh_preview(self):
        image = self.current_image()
        if image is None:
            self.canvas.set_pixmap(None)
            return
        # Preview at a sane size - the edits themselves are full resolution.
        preview = image.copy()
        preview.thumbnail((1400, 1400), RESAMPLE_LANCZOS)
        # keep_view so brushing doesn't throw the user back out to Fit.
        self.canvas.set_pixmap(QPixmap.fromImage(pil_to_qimage(preview)),
                               keep_view=not self._reset_view_next)
        self._reset_view_next = False

    def set_status(self, text):
        self.status_label.setText(text)

    def _update_controls(self):
        loaded = self.source is not None
        self._update_zoom_controls()
        for widget in (self.auto_button, self.undo_button, self.reset_button,
                       self.save_button, self.tolerance_slider, self.brush_slider,
                       self.contiguous_check, *self.tool_buttons.values()):
            widget.setEnabled(loaded)
        self.ai_button.setEnabled(loaded and ONNX_AVAILABLE and not self.busy)
        self.model_combo.setEnabled(ONNX_AVAILABLE and not self.busy)
        self.undo_button.setEnabled(loaded and bool(self.history))

    def set_tool(self, tool):
        self.tool = tool
        for name, button in self.tool_buttons.items():
            button.setChecked(name == tool)
        self.set_status({
            self.TOOL_WAND: "Click a background colour in the preview to clear it.",
            self.TOOL_ERASE: "Drag over the preview to rub parts away.",
            self.TOOL_RESTORE: "Drag over the preview to bring parts back.",
        }[tool])

    # -- Removal operations ----------------------------------------------------

    def _clear_mask(self, mask):
        """Make every pixel in `mask` transparent."""
        alpha = _alpha_array(self.alpha)
        alpha[mask] = 0
        self.alpha = Image.fromarray(alpha, mode="L")

    def remove_at(self, x, y):
        if self.source is None:
            return
        if not (0 <= x < self.source.width and 0 <= y < self.source.height):
            return
        colour = tuple(int(v) for v in self.rgb_array[y, x])
        tolerance = self.tolerance_slider.value()
        similar = colour_match_mask(self.rgb_array, colour, tolerance)
        # Already-transparent pixels shouldn't block a flood fill.
        similar |= _alpha_array(self.alpha) == 0

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            mask = flood_region(similar, x, y) if self.contiguous_check.isChecked() else similar
            self._push_history()
            self._clear_mask(mask)
        finally:
            QApplication.restoreOverrideCursor()

        cleared = int(mask.sum())
        total = mask.size
        self.set_status(
            f"Cleared {cleared:,} pixels ({cleared / total:.0%} of the image). "
            "Adjust Tolerance if it took too much or too little, then Undo and retry."
        )
        self.refresh_preview()

    def remove_edge_background(self):
        """Clear everything connected to the four corners that matches them."""
        if self.source is None:
            return
        width, height = self.source.size
        tolerance = self.tolerance_slider.value()
        corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            transparent = _alpha_array(self.alpha) == 0
            combined = np.zeros((height, width), dtype=bool)
            for x, y in corners:
                colour = tuple(int(v) for v in self.rgb_array[y, x])
                similar = colour_match_mask(self.rgb_array, colour, tolerance) | transparent
                combined |= flood_region(similar, x, y)
            self._push_history()
            self._clear_mask(combined)
        finally:
            QApplication.restoreOverrideCursor()

        cleared = int(combined.sum())
        if cleared == 0:
            self.set_status("Nothing matched at the corners — try a higher Tolerance.")
        else:
            self.set_status(
                f"Cleared {cleared / combined.size:.0%} of the image from the corners. "
                "Use Erase and Restore to tidy up what's left."
            )
        self.refresh_preview()

    AI_BUTTON_LABEL = "AI: find the subject and cut it out"

    def run_ai_cutout(self):
        if self.source is None or not ONNX_AVAILABLE or self.busy:
            return
        model_file = AI_MODELS[self.model_combo.currentText()][0]
        self.busy = True
        self._update_controls()
        self.ai_button.setText("Working…")
        self.ai_progress.setValue(0)
        self.ai_progress.setVisible(not os.path.exists(ai_model_path(model_file)))
        self.set_status("Working out what the subject is…")

        # Run on the original image rather than the current edit, so the AI
        # decides for itself instead of inheriting earlier mistakes.
        worker = CutoutWorker(self.source, model_file)
        worker.signals.status.connect(self.set_status)
        worker.signals.progress.connect(
            lambda fraction: self.ai_progress.setValue(int(fraction * 100))
        )
        worker.signals.finished.connect(self._ai_finished)
        worker.signals.failed.connect(self._ai_failed)
        self.main_window.thread_pool.start(worker)

    def _ai_finished(self, alpha, message):
        self.busy = False
        self.ai_button.setText(self.AI_BUTTON_LABEL)
        self.ai_progress.setVisible(False)
        self._push_history()
        self.alpha = alpha
        self._update_controls()
        self.set_status(
            message + " Use Erase and Restore to fix anything it got wrong, "
            "or try a different model above."
        )
        self.refresh_preview()

    def _ai_failed(self, error_message):
        self.busy = False
        self.ai_button.setText(self.AI_BUTTON_LABEL)
        self.ai_progress.setVisible(False)
        self._update_controls()
        self.set_status("AI cutout failed.")
        show_critical(self, "AI cutout failed", error_message)

    # -- Brushes ---------------------------------------------------------------

    def _paint_brush(self, x, y, value):
        from PIL import ImageDraw
        radius = max(1, self.brush_slider.value() // 2)
        draw = ImageDraw.Draw(self.alpha)
        if self._last_brush_point is not None:
            # Join up to the previous point so quick drags don't leave gaps.
            draw.line([self._last_brush_point, (x, y)], fill=value, width=radius * 2)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=value)
        self._last_brush_point = (x, y)

    def on_canvas_pressed(self, x, y):
        if self.source is None:
            return
        if self.tool == self.TOOL_WAND:
            self.remove_at(x, y)
            return
        self._push_history()
        self._last_brush_point = None
        self._paint_brush(x, y, 0 if self.tool == self.TOOL_ERASE else 255)
        self.refresh_preview()

    def on_canvas_dragged(self, x, y):
        if self.source is None or self.tool == self.TOOL_WAND:
            return
        self._paint_brush(x, y, 0 if self.tool == self.TOOL_ERASE else 255)
        self.refresh_preview()

    def on_canvas_released(self):
        self._last_brush_point = None

    # -- Edge tidying ----------------------------------------------------------

    def soften_edges(self):
        if self.source is None:
            return
        from PIL import ImageFilter
        self._push_history()
        self.alpha = self.alpha.filter(ImageFilter.GaussianBlur(1.2))
        self.set_status("Softened the cut edge.")
        self.refresh_preview()

    def tighten_edges(self):
        if self.source is None:
            return
        from PIL import ImageFilter
        self._push_history()
        # MinFilter pulls the edge inwards by a pixel, which takes the halo of
        # background colour with it.
        self.alpha = self.alpha.filter(ImageFilter.MinFilter(3))
        self.set_status("Pulled the edge in by a pixel.")
        self.refresh_preview()

    def trim_to_subject(self):
        if self.source is None:
            return
        bbox = self.alpha.getbbox()
        if bbox is None:
            self.set_status("Everything is transparent — nothing left to trim to.")
            return
        if bbox == (0, 0, self.source.width, self.source.height):
            self.set_status("Nothing to trim — the subject already fills the image.")
            return
        self._push_history()
        self.source = self.source.crop(bbox)
        self.alpha = self.alpha.crop(bbox)
        self.rgb_array = np.array(self.source.convert("RGB"), dtype=np.uint8)
        self.history = []  # sizes no longer match, so old steps can't be restored
        self.undo_button.setEnabled(False)
        self._reset_view_next = True
        self.set_status(f"Trimmed to {self.source.width} × {self.source.height}.")
        self.refresh_preview()

    # -- Saving ----------------------------------------------------------------

    def _confirm_zoomed_save(self):
        """Asks before saving while the preview is zoomed in.

        Zoom is only a viewing aid here - the whole image is saved either
        way, nothing is cropped - so this offers to zoom back out first
        rather than pretending the zoom changes the output.
        """
        if not self.canvas.is_zoomed():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("The preview is zoomed in")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"The preview is zoomed to {self.canvas.zoom * 100:.0f}%, so you "
            "can only see part of the image."
        )
        box.setInformativeText(
            "Zooming only changes what you're looking at — the whole image is "
            "saved either way, and nothing gets cropped. Zoom back out first "
            "if you'd like to check the rest of it before saving."
        )
        save_button = box.addButton("Save the whole image", QMessageBox.AcceptRole)
        reset_button = box.addButton("Zoom out and look first", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(save_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked is reset_button:
            self.canvas.reset_view()
            self.set_status("Zoomed back out — press Save Image when you're happy.")
            return False
        return clicked is save_button

    def save_image(self):
        image = self.current_image()
        if image is None:
            return
        if not self._confirm_zoomed_save():
            return
        fmt = self.format_combo.currentText().lower()
        suffix = "." + fmt
        # Keep the original file name; only the extension follows the format.
        base = os.path.splitext(os.path.basename(self.source_path))[0] or "cutout"
        default = os.path.join(os.path.dirname(self.source_path), base + suffix)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save image", default, f"{fmt.upper()} Image (*{suffix})"
        )
        if not path:
            return
        if not path.lower().endswith(suffix):
            path += suffix
        try:
            _web_save_still(image, path, fmt)
        except Exception as exc:  # noqa: BLE001
            show_critical(self, "Save failed", str(exc))
            return
        self.set_status(f"Saved {os.path.basename(path)}")


class BackgroundRemoverUnavailableTab(QWidget):
    """Stand-in when NumPy isn't installed - every removal tool needs it."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        box = QGroupBox("Background Remover unavailable")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(18, 16, 18, 18)
        message = QLabel(
            "Removing backgrounds needs NumPy, which isn't installed in this "
            "Python.\n\nInstall it with:\n    pip install numpy\n\n"
            "For the AI cutout as well:\n"
            "    pip install onnxruntime"
        )
        message.setWordWrap(True)
        box_layout.addWidget(message)
        box_layout.addStretch()
        layout.addWidget(box, 1)


# ---------------------------------------------------------------------------
# Image Creation tab
#
# Local text-to-image generation through Stable Diffusion. Everything runs on
# this machine - the model is downloaded once and then used offline, and no
# prompt or image ever leaves the PC.
#
# The heavy lifting is done by PyTorch + diffusers, which are big installs and
# not bundled. When they're missing the tab explains how to add them instead
# of disappearing.
#
# load_generation_pipeline() and run_generation_pipeline() are deliberately
# the only two places that touch diffusers, which keeps everything else in
# this tab testable without a model present.
# ---------------------------------------------------------------------------

# Catch everything, not just ImportError: a torch that's installed but can't
# load its DLLs raises OSError instead, and "installed yet unusable" needs to
# be reported rather than crashing the app or looking like "not installed".
TORCH_IMPORT_ERROR = None
DIFFUSERS_IMPORT_ERROR = None

try:
    import torch
    TORCH_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    torch = None
    TORCH_AVAILABLE = False
    TORCH_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

try:
    import diffusers  # noqa: F401
    DIFFUSERS_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    DIFFUSERS_AVAILABLE = False
    DIFFUSERS_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

GENERATION_AVAILABLE = TORCH_AVAILABLE and DIFFUSERS_AVAILABLE

# PyTorch publishes each CUDA build on its own index. cu121 was retired, and
# the older indexes never got wheels for the newest Pythons - which is what
# "No matching distribution found for torch" usually means. These are the
# builds PyTorch currently offers; the picker on their site is the last word.
TORCH_CUDA_INDEXES = [
    ("CUDA 12.8 — newest NVIDIA cards", "cu128"),
    ("CUDA 12.6 — most NVIDIA cards", "cu126"),
    ("CUDA 11.8 — older NVIDIA cards", "cu118"),
]
TORCH_SELECTOR_URL = "https://pytorch.org/get-started/locally/"
GENERATION_PACKAGES = "diffusers transformers accelerate safetensors"


def refresh_generation_backend():
    """Re-checks for torch/diffusers so they can be installed without
    restarting the app."""
    global torch, TORCH_AVAILABLE, DIFFUSERS_AVAILABLE, GENERATION_AVAILABLE
    global TORCH_IMPORT_ERROR, DIFFUSERS_IMPORT_ERROR
    import importlib
    try:
        importlib.invalidate_caches()
        torch = importlib.import_module("torch")
        TORCH_AVAILABLE = True
        TORCH_IMPORT_ERROR = None
    except Exception as exc:  # noqa: BLE001
        TORCH_AVAILABLE = False
        TORCH_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    try:
        importlib.import_module("diffusers")
        DIFFUSERS_AVAILABLE = True
        DIFFUSERS_IMPORT_ERROR = None
    except Exception as exc:  # noqa: BLE001
        DIFFUSERS_AVAILABLE = False
        DIFFUSERS_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    GENERATION_AVAILABLE = TORCH_AVAILABLE and DIFFUSERS_AVAILABLE
    return GENERATION_AVAILABLE


def python_description():
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    version = ".".join(str(part) for part in sys.version_info[:3])
    return f"Python {version} ({bits}-bit)"


def generation_diagnostics():
    """Everything needed to work out why the packages aren't being found.

    By far the most common cause is more than one Python on the machine: pip
    puts the packages in one, while the app runs in another, so pip keeps
    saying "already satisfied" while the app keeps saying "not installed".
    """
    lines = [
        f"App is running in: {sys.executable or 'unknown'}",
        f"Version: {python_description()}",
        f"Packaged .exe: {'yes' if getattr(sys, 'frozen', False) else 'no'}",
        "",
        f"PyTorch: {'found' if TORCH_AVAILABLE else 'not usable'}",
    ]
    if TORCH_IMPORT_ERROR and not TORCH_AVAILABLE:
        lines.append(f"    {TORCH_IMPORT_ERROR}")
    if TORCH_AVAILABLE:
        lines.append(f"    version {getattr(torch, '__version__', '?')}, "
                     f"CUDA available: {bool(torch.cuda.is_available())}")
    lines.append(f"diffusers: {'found' if DIFFUSERS_AVAILABLE else 'not usable'}")
    if DIFFUSERS_IMPORT_ERROR and not DIFFUSERS_AVAILABLE:
        lines.append(f"    {DIFFUSERS_IMPORT_ERROR}")
        if "metadata" in DIFFUSERS_IMPORT_ERROR.lower():
            lines.append("")
            lines.append(
                "    That's a packaging problem, not a missing install: the "
                "code was bundled but its package metadata wasn't, and "
                "diffusers/transformers read that at import. Rebuild with an "
                "up-to-date build.bat, which passes --recursive-copy-metadata "
                "for them."
            )
    if TORCH_AVAILABLE and not torch.cuda.is_available():
        lines.append("")
        build = getattr(torch, "__version__", "")
        if "+cpu" in build:
            lines.append(
                f"    PyTorch {build} is the CPU-only build, so generation "
                "would run on the processor - minutes per image. For GPU "
                "speed, reinstall from a CUDA index (see the panel)."
            )
        else:
            lines.append(
                "    PyTorch can't see a usable GPU, so generation would run "
                "on the processor."
            )

    lines.append("")
    lines.append("Where Python looks for packages:")
    for path in sys.path[:8]:
        lines.append(f"    {path or '(current folder)'}")
    return "\n".join(lines)


def generation_install_command():
    """A pip command tied to this exact interpreter, which sidesteps the
    'installed into a different Python' trap."""
    executable = sys.executable or "python"
    if getattr(sys, "frozen", False):
        executable = "python"
    quoted = f'"{executable}"' if " " in executable else executable
    return (f"{quoted} -m pip install torch --index-url "
            f"https://download.pytorch.org/whl/cu126 && "
            f"{quoted} -m pip install {GENERATION_PACKAGES}")


def generation_install_help():
    """What to actually run, for this Python, right now."""
    lines = []
    if getattr(sys, "frozen", False):
        lines.append(
            "This is the packaged .exe, which carries its own copy of Python. "
            "Installing packages with pip won't reach it — image generation "
            "has to be installed into the Python you build the app with, and "
            "then the app rebuilt. Running the app from "
            "image_to_png_converter.py instead is the easier route."
        )
        lines.append("")

    lines.append(f"Generating images needs PyTorch and diffusers. This app is "
                 f"running {python_description()} from:")
    lines.append(f"    {sys.executable or 'unknown'}")
    lines.append("")
    lines.append("If pip keeps saying \"Requirement already satisfied\" while this "
                 "still says they're missing, they went into a different Python. "
                 "Use the button below, which installs into the one above.")
    lines.append("")
    lines.append("With an NVIDIA graphics card, pick the CUDA build that suits it:")
    for label, tag in TORCH_CUDA_INDEXES:
        lines.append(f"    pip install torch --index-url "
                     f"https://download.pytorch.org/whl/{tag}      ({label})")
    lines.append("")
    lines.append("Without one (slower, but it works):")
    lines.append("    pip install torch")
    lines.append("")
    lines.append("Then, either way:")
    lines.append(f"    pip install {GENERATION_PACKAGES}")
    lines.append("")

    if sys.version_info >= (3, 13):
        lines.append(
            "Note: Python 3.13+ is new enough that some CUDA builds may not "
            "have wheels for it yet. If every command above says \"No matching "
            "distribution found\", that's why — use the picker on "
            f"{TORCH_SELECTOR_URL} to see what's available, or install "
            "Python 3.12 alongside and run the app with that."
        )
    else:
        lines.append(
            "If pip says \"No matching distribution found for torch\", that "
            "CUDA build has no wheel for your Python. Use the picker on "
            f"{TORCH_SELECTOR_URL} to get the exact command."
        )
    lines.append("")
    lines.append("Restart the app afterwards, or press Check again.")
    return "\n".join(lines)

# The models, roughly best-first. "family" decides how the pipeline is
# driven, because FLUX and SD3 take different arguments to SD/SDXL.
#
# Nothing that runs on a home PC matches a hosted model like GPT-4o's image
# generation - those are far larger and run on datacentre hardware. FLUX.1 is
# the closest thing available to run locally, and it's very good, but it wants
# a strong GPU. The lighter models below are there for when it doesn't fit.
GENERATION_MODELS = {
    "FLUX.1 schnell — closest to online models": {
        "id": "black-forest-labs/FLUX.1-schnell",
        "size": 1024, "family": "flux", "steps": 4, "guidance": 0,
            "note": "About 24 GB to download, and happiest with 12 GB+ of video memory. "
        "Only needs a few steps. Free, but you have to accept its terms on "
        "the model's page and add a Hugging Face token first.",
    },
    "FLUX.1 dev — best quality, slower": {
        "id": "black-forest-labs/FLUX.1-dev",
        "size": 1024, "family": "flux", "steps": 28, "guidance": 4,
            "note": "About 24 GB, 12 GB+ of video memory, and you have to accept its "
        "licence on the model's page before it will download.",
    },
    "SD 3.5 Medium — strong, lighter": {
        "id": "stabilityai/stable-diffusion-3.5-medium",
        "size": 1024, "family": "sd3", "steps": 28, "guidance": 5,
            "note": "About 5 GB. Needs the licence accepting on the model's page first.",
    },
    "SDXL 1.0 — reliable, no sign-up": {
        "id": "stabilityai/stable-diffusion-xl-base-1.0",
        "size": 1024, "family": "sdxl", "steps": 25, "guidance": 7,
            "note": "About 7 GB. Works anywhere, no sign-up needed.",
    },
    "SDXL Turbo — fast": {
        "id": "stabilityai/sdxl-turbo",
        "size": 512, "family": "sdxl", "steps": 4, "guidance": 0,
            "note": "About 7 GB. Built for 1-4 steps, so it is quick.",
    },
    "SD Turbo — fastest, smallest": {
        "id": "stabilityai/sd-turbo",
        "size": 512, "family": "sd", "steps": 4, "guidance": 0,
            "note": "About 2.5 GB. The one to try on a modest PC or without a GPU.",
    },
    "Dreamshaper 8 — light all-rounder": {
        "id": "Lykon/dreamshaper-8",
        "size": 512, "family": "sd", "steps": 25, "guidance": 7,
            "note": "About 2 GB. An older but well-liked general model.",
    },
    "Custom model…": {
        "id": "", "size": 1024, "family": "sdxl", "steps": 25, "guidance": 7,
            "note": "Any model id from Hugging Face, or a folder on this PC.",
    },
}

# Families that are too big to sit in VRAM comfortably; diffusers can shuffle
# their parts on and off the GPU as needed instead of failing outright.
LARGE_FAMILIES = {"flux", "sd3"}

GENERATION_SIZES = ["512 × 512", "768 × 768", "1024 × 1024",
                    "768 × 512 (landscape)", "512 × 768 (portrait)",
                    "1024 × 576 (16:9)", "576 × 1024 (9:16)"]

_GENERATION_PIPELINES = {}


class GenerationCancelled(Exception):
    """Raised inside the model's per-step callback to stop early."""


def generation_device():
    """cuda when there's a usable GPU, otherwise cpu."""
    if TORCH_AVAILABLE and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def generation_device_description():
    if not GENERATION_AVAILABLE:
        return "not installed"
    if generation_device() == "cuda":
        try:
            return f"GPU — {torch.cuda.get_device_name(0)}"
        except Exception:  # noqa: BLE001
            return "GPU"
    if TORCH_AVAILABLE and "+cpu" in getattr(torch, "__version__", ""):
        return ("CPU only — this is the CPU build of PyTorch, so expect "
                "minutes per image. Reinstall it from a CUDA index for GPU speed.")
    return "CPU only — expect minutes per image rather than seconds"


HF_TOKEN_FILENAME = "hf_token.txt"

# The token typed into the box this session, remembered or not.
_session_hf_token = ""


def user_data_folder():
    """A per-user folder that's always writable, unlike Program Files."""
    base = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_TITLE)


def hf_token_path():
    return os.path.join(user_data_folder(), HF_TOKEN_FILENAME)


def load_remembered_hf_token():
    """The token saved with the Remember button, or ""."""
    try:
        with open(hf_token_path(), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        pass
    # Older versions saved it in the app settings; move it to the file.
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    old = (settings.value("hf_token", "") or "").strip()
    if old:
        try:
            remember_hf_token(old)
            settings.remove("hf_token")
        except OSError:
            pass
    return old


def remember_hf_token(token):
    """Writes the token to a file in the user's own folder, readable only
    by them where the system supports it."""
    os.makedirs(user_data_folder(), exist_ok=True)
    path = hf_token_path()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(token.strip())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def forget_hf_token():
    try:
        os.remove(hf_token_path())
    except OSError:
        pass
    QSettings(SETTINGS_ORG, SETTINGS_APP).remove("hf_token")


def set_session_hf_token(token):
    global _session_hf_token
    _session_hf_token = (token or "").strip()


def huggingface_token():
    """The token in the box, then the remembered one, then the standard
    environment variables so an existing Hugging Face login keeps working."""
    return (_session_hf_token or load_remembered_hf_token()
            or os.environ.get("HF_TOKEN", "")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN", ""))


class ModelAccessError(Exception):
    """The model can't be downloaded with the current token. The message
    says what to do about it."""


def check_model_access(model_id):
    """Asks Hugging Face whether this model can be downloaded, before
    starting a multi-gigabyte download that would only fail.

    Raises ModelAccessError with step-by-step instructions when it can't.
    Anything inconclusive (no internet, an old huggingface_hub, a local
    folder, a model already downloaded) is let through for the download
    itself to report.
    """
    if not model_id or os.path.isdir(model_id):
        return
    try:
        from huggingface_hub import auth_check, try_to_load_from_cache
        from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError
    except ImportError:
        return
    try:
        if isinstance(try_to_load_from_cache(model_id, "model_index.json"), str):
            return  # already downloaded, works offline
    except Exception:  # noqa: BLE001
        pass

    token = huggingface_token() or None
    page = f"https://huggingface.co/{model_id}"
    try:
        auth_check(model_id, token=token)
    except GatedRepoError:
        if token:
            raise ModelAccessError(
                f"Your Hugging Face token doesn't have access to {model_id} "
                "yet.\n\n"
                f"1. Open {page} while signed in to the same Hugging Face "
                "account the token belongs to.\n"
                "2. Accept the terms at the top of the page (\"Agree and access "
                "repository\").\n"
                "3. Try again - access is usually granted instantly."
            ) from None
        raise ModelAccessError(
            f"{model_id} is free, but Hugging Face only lets you download it "
            "once you've accepted its terms and the app has a token.\n\n"
            "1. Sign in or make a free account at huggingface.co.\n"
            f"2. Open {page} and accept the terms at the top of the page.\n"
            "3. Create a token at huggingface.co/settings/tokens - the "
            "\"Read\" type is enough.\n"
            "4. Paste it into the HF token box in this tab's settings, then "
            "try again.\n\n"
            "The \"How to get a token\" button next to that box walks "
            "through these steps with clickable links.\n\n"
            "Or pick SDXL or SD Turbo, which need none of this."
        ) from None
    except RepositoryNotFoundError:
        if token:
            raise ModelAccessError(
                f"Hugging Face says {model_id} doesn't exist or your token "
                "can't see it.\n\n"
                "Check the model name, and that the token in the HF token box "
                "is current - you can make a new one at "
                "huggingface.co/settings/tokens."
            ) from None
        raise ModelAccessError(
            f"Hugging Face says there's no model called {model_id}.\n\n"
            "Check the name against the model's page. If it's a private or "
            "gated model, add a token in the HF token box first."
        ) from None
    except Exception:  # noqa: BLE001
        return


def explain_model_error(message, model_id):
    """Turns a diffusers/hub error into something you can act on."""
    prefix = "ModelAccessError: "
    if message.startswith(prefix):
        return message[len(prefix):].split("\n\nTraceback", 1)[0]
    lowered = message.lower()
    if ("not a valid model identifier" in lowered or "401" in lowered
            or "403" in lowered or "gated" in lowered or "restricted" in lowered):
        return (
            f"Couldn't fetch {model_id}.\n\n"
            "The usual reasons, most likely first:\n\n"
            "1. The model needs its licence accepting. Both FLUX.1 models and "
            "SD 3.5 are gated - open the model's page on huggingface.co while signed "
            "in, accept the terms, then paste an access token into the HF "
            "token box in Settings.\n\n"
            "2. No internet, or it's being blocked. The first use of a model "
            "downloads it, so a firewall or proxy will stop it.\n\n"
            "3. The name is wrong - if you typed a custom one, check it "
            "against the model's page.\n\n"
            "SDXL and SD Turbo need no token, so if those fail "
            "too it's almost certainly the connection."
        )
    if "out of memory" in lowered or "cuda oom" in lowered:
        return ("The graphics card ran out of memory.\n\n"
                "Try a smaller model, or a smaller output size.")
    return message


def load_generation_pipeline(model_id, use_img2img, family="sdxl"):
    """Loads (and caches) a diffusers pipeline. The model downloads itself on
    first use into the usual Hugging Face cache folder, then runs offline."""
    from diffusers import AutoPipelineForImage2Image, AutoPipelineForText2Image

    device = generation_device()
    key = (model_id, use_img2img, device)
    if key in _GENERATION_PIPELINES:
        return _GENERATION_PIPELINES[key]

    if device == "cuda":
        # bfloat16 is what FLUX and SD3 are built for; fp16 suits the rest.
        dtype = torch.bfloat16 if family in LARGE_FAMILIES else torch.float16
    else:
        dtype = torch.float32

    base_key = (model_id, False, device)
    if use_img2img and base_key in _GENERATION_PIPELINES:
        # Reuses the weights already in memory rather than a second copy.
        pipeline = AutoPipelineForImage2Image.from_pipe(_GENERATION_PIPELINES[base_key])
    else:
        loader = AutoPipelineForImage2Image if use_img2img else AutoPipelineForText2Image
        arguments = {"torch_dtype": dtype}
        if family in ("sd", "sdxl"):
            arguments["safety_checker"] = None
        # FLUX.1 dev and SD 3.5 sit behind a licence you accept on their page,
        # and then only download with an access token.
        token = huggingface_token()
        if token:
            arguments["token"] = token
        pipeline = loader.from_pretrained(model_id, **arguments)

        offloaded = False
        if device == "cuda" and family in LARGE_FAMILIES:
            try:
                # Keeps only the part that's working on the GPU, so the big
                # models still run on a card that couldn't hold them whole.
                pipeline.enable_model_cpu_offload()
                offloaded = True
            except Exception:  # noqa: BLE001
                offloaded = False
        if not offloaded:
            pipeline = pipeline.to(device)

        for tweak in ("enable_attention_slicing", "enable_vae_tiling"):
            try:
                getattr(pipeline, tweak)()
            except Exception:  # noqa: BLE001
                pass

    _GENERATION_PIPELINES[key] = pipeline
    return pipeline


def run_generation_pipeline(pipeline, request, on_step):
    """Runs one generation and returns a PIL image.

    on_step(step, total) is called as it goes and may raise
    GenerationCancelled to stop.
    """
    generator = None
    if request.get("seed") is not None:
        generator = torch.Generator(device=generation_device())
        generator.manual_seed(int(request["seed"]))

    steps = request["steps"]

    def callback(pipe, step_index, timestep, callback_kwargs):
        on_step(step_index + 1, steps)
        return callback_kwargs

    arguments = {
        "prompt": request["prompt"],
        "num_inference_steps": steps,
        "guidance_scale": request["guidance"],
        "generator": generator,
        "callback_on_step_end": callback,
    }
    # FLUX has no negative prompt - it's distilled without one - so passing
    # it through would just raise.
    if request.get("negative_prompt") and request.get("family") != "flux":
        arguments["negative_prompt"] = request["negative_prompt"]
    if request.get("family") == "flux":
        arguments["max_sequence_length"] = 256

    init_image = request.get("init_image")
    if init_image is not None:
        arguments["image"] = init_image
        arguments["strength"] = request["strength"]
    else:
        arguments["width"], arguments["height"] = request["size"]

    return pipeline(**arguments).images[0]


def blend_reference_images(images, size):
    """Combines reference images into a single starting picture.

    Each one is cropped to fill the output shape and then averaged together,
    so two references genuinely mix rather than one hiding the other.
    """
    if not images:
        return None
    prepared = [
        resize_to_spec(image, {"size": size, "mode": "fill", "upscale": True}).convert("RGB")
        for image in images
    ]
    blended = prepared[0]
    for index, image in enumerate(prepared[1:], start=2):
        # Equal weighting across however many were added.
        blended = Image.blend(blended, image, 1.0 / index)
    return blended


class GenerationSignals(QObject):
    progress = pyqtSignal(int, int)      # step, total
    status = pyqtSignal(str)
    finished = pyqtSignal(object, int)   # image, seed used
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()


class GenerationWorker(QRunnable):
    """Loads the model if needed and generates one image, off the GUI thread."""

    def __init__(self, request):
        super().__init__()
        self.request = request
        self.signals = GenerationSignals()
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _on_step(self, step, total):
        if self._cancel:
            raise GenerationCancelled()
        self.signals.progress.emit(step, total)

    def run(self):
        try:
            self.signals.status.emit(
                "Loading the model… the first time also downloads it, which "
                "can take a while."
            )
            check_model_access(self.request["model_id"])
            pipeline = load_generation_pipeline(
                self.request["model_id"],
                self.request.get("init_image") is not None,
                self.request.get("family", "sdxl"),
            )
            if self._cancel:
                self.signals.cancelled.emit()
                return
            self.signals.status.emit("Generating…")
            image = run_generation_pipeline(pipeline, self.request, self._on_step)
            self.signals.finished.emit(image, self.request["seed"])
        except GenerationCancelled:
            self.signals.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(
                f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc().rstrip()}")


class ReferenceList(QListWidget):
    """The reference images, shown as thumbnails."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(84, 84))
        self.setGridSize(QSize(96, 96))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setFixedHeight(120)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setAcceptDrops(True)
        self.paths = []

    def add_path(self, path):
        try:
            image = Image.open(path)
            image.load()
        except Exception:  # noqa: BLE001
            return False
        thumbnail = image.convert("RGBA")
        thumbnail.thumbnail((84, 84), RESAMPLE_LANCZOS)
        item = QListWidgetItem(QIcon(QPixmap.fromImage(pil_to_qimage(thumbnail))), "")
        item.setToolTip(os.path.basename(path))
        self.addItem(item)
        self.paths.append(path)
        return True

    def remove_selected(self):
        for item in self.selectedItems():
            row = self.row(item)
            self.takeItem(row)
            del self.paths[row]

    def clear_all(self):
        self.clear()
        self.paths = []

    def images(self):
        loaded = []
        for path in self.paths:
            try:
                with Image.open(path) as image:
                    loaded.append(image.convert("RGB").copy())
            except Exception:  # noqa: BLE001
                continue
        return loaded

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.add_path(url.toLocalFile())
        event.acceptProposedAction()


class HFTokenGuideDialog(QDialog):
    """Step-by-step help for getting a Hugging Face token. Each step's link
    opens in the browser and ticks the step off, so it's easy to see where
    you got to."""

    LINK_COLOUR = "#9d7bff"
    TICK_COLOUR = "#2ecc71"
    # Steps already done this session, kept across re-openings.
    done_steps = set()

    def __init__(self, tab, model_id):
        super().__init__(tab)
        self.tab = tab
        self.setWindowTitle("How to get a Hugging Face token")
        # A fixed width lets the wrapped steps work out their full height.
        self.setFixedWidth(560)
        model_id = model_id or "black-forest-labs/FLUX.1-schnell"
        model_page = f"https://huggingface.co/{model_id}"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        intro = QLabel(
            "Some models, including both FLUX.1 models and SD 3.5, are free "
            "but only download once you've accepted their terms and given "
            "the app a token. It takes a couple of minutes, once."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        steps = [
            ("account", "Sign in, or make a free account, at "
             "{link}.", "https://huggingface.co/login", "huggingface.co"),
            ("terms", "Open {link} and accept the terms at the top of the "
             "page (\"Agree and access repository\").", model_page,
             f"the {model_id} page"),
            ("token", "Create a token at {link}. Click \"Create new token\", "
             "pick the \"Read\" type and copy it.",
             "https://huggingface.co/settings/tokens",
             "huggingface.co/settings/tokens"),
        ]
        self.ticks = {}
        for number, (key, text, url, link_text) in enumerate(steps, start=1):
            link = (f'<a href="{url}" style="color:{self.LINK_COLOUR};">'
                    f'{link_text}</a>')
            layout.addLayout(self._step_row(
                key, number, text.format(link=link), url))

        # Step 4 has no link - it's done here, by pasting the token in.
        paste_row = self._step_row(
            "paste", 4, "Paste the token below and press Remember, so the app "
            "keeps it for next time.", None)
        layout.addLayout(paste_row)

        token_row = QHBoxLayout()
        token_row.setContentsMargins(34, 0, 0, 0)
        token_row.setSpacing(8)
        self.token_edit = QLineEdit(tab.token_edit.text())
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("hf_…")
        token_row.addWidget(self.token_edit, 1)
        remember_button = QPushButton("Remember")
        remember_button.setObjectName("ConvertButton")
        remember_button.setCursor(Qt.PointingHandCursor)
        remember_button.clicked.connect(self.remember)
        token_row.addWidget(remember_button)
        layout.addLayout(token_row)

        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close_button = QPushButton("Close")
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        if load_remembered_hf_token():
            self.done_steps.add("paste")
        for key in self.done_steps:
            if key in self.ticks:
                self.ticks[key].setText("\u2714")
        self.resize(self.width(), self.heightForWidth(self.width()))

    def _step_row(self, key, number, html, url):
        row = QHBoxLayout()
        row.setSpacing(10)
        tick = QLabel("")
        tick.setFixedSize(22, 22)
        tick.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        tick.setStyleSheet(
            f"color: {self.TICK_COLOUR}; font-size: 16px; font-weight: bold;")
        tick.setToolTip("Done")
        self.ticks[key] = tick
        row.addWidget(tick, 0, Qt.AlignTop)
        text = QLabel(f"<b>{number}.</b> {html}")
        text.setTextFormat(Qt.RichText)
        text.setWordWrap(True)
        text.setOpenExternalLinks(False)
        text.setTextInteractionFlags(Qt.TextBrowserInteraction)
        text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        if url:
            text.linkActivated.connect(
                lambda link, step=key: self.open_step(step, link))
        row.addWidget(text, 1)
        return row

    def mark_done(self, key):
        self.done_steps.add(key)
        self.ticks[key].setText("\u2714")

    def open_step(self, key, url):
        QDesktopServices.openUrl(QUrl(url))
        self.mark_done(key)

    def remember(self):
        token = self.token_edit.text().strip()
        if not token:
            self.token_edit.setFocus()
            return
        self.tab.token_edit.setText(token)
        if self.tab.remember_token():
            self.mark_done("paste")


class ImageCreationTab(QWidget):
    """Describe a picture, optionally hand it reference images, and generate
    it locally."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.result = None
        self.worker = None
        self.busy = False
        self._build_ui()
        self._update_controls()

    # -- UI --------------------------------------------------------------------

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 14, 22, 14)
        root.setSpacing(18)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(14)
        panel_layout.addWidget(self._build_settings_group())
        panel_layout.addWidget(self._build_prompt_group())
        panel_layout.addWidget(self._build_reference_group())
        panel_layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(300)

        result_box = QGroupBox("Result")
        result_layout = QVBoxLayout(result_box)
        result_layout.setContentsMargins(16, 16, 16, 16)
        result_layout.setSpacing(12)

        self.result_label = QLabel(
            "Describe what you want and press Generate."
            if GENERATION_AVAILABLE else
            "Image generation isn't set up yet — see the panel on the left."
        )
        self.result_label.setObjectName("PreviewBox")
        self.result_label.setAlignment(Qt.AlignCenter)
        self.result_label.setMinimumHeight(360)
        result_layout.addWidget(self.result_label, 1)

        self.progress = QProgressBar()
        self.progress.setObjectName("NiceProgressBar")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        self.progress.setVisible(False)
        result_layout.addWidget(self.progress)

        self.status_label = QLabel(f"Runs on: {generation_device_description()}")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        result_layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.save_button = QPushButton("Save Image…")
        self.save_button.setObjectName("ConvertButton")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.clicked.connect(self.save_result)
        actions.addWidget(self.save_button)

        self.use_as_reference_button = QPushButton("Use as reference")
        self.use_as_reference_button.setCursor(Qt.PointingHandCursor)
        self.use_as_reference_button.setToolTip(
            "Feeds this result back in as a starting image, so you can nudge "
            "it with another prompt."
        )
        self.use_as_reference_button.clicked.connect(self.use_result_as_reference)
        actions.addWidget(self.use_as_reference_button)

        self.send_to_remover_button = QPushButton("Remove background")
        self.send_to_remover_button.setCursor(Qt.PointingHandCursor)
        self.send_to_remover_button.setToolTip("Opens this result in the Background Remover")
        self.send_to_remover_button.clicked.connect(self.send_result_to_remover)
        actions.addWidget(self.send_to_remover_button)
        actions.addStretch()
        result_layout.addLayout(actions)

        # A draggable divider rather than a fixed-width panel, so the
        # description side and the result side can each be given as much
        # room as the job needs.
        self.splitter = GripSplitter(Qt.Horizontal)
        self.splitter.setObjectName("BrowserSplitter")
        self.splitter.setHandleWidth(14)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(scroll)
        self.splitter.addWidget(result_box)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        result_box.setMinimumWidth(300)
        root.addWidget(self.splitter, 1)
        self._sized_splitter = False

    def showEvent(self, event):
        super().showEvent(event)
        # Give the controls a comfortable starting width the first time,
        # then leave wherever the user drags the divider alone.
        if not self._sized_splitter and self.splitter.width() > 100:
            self._sized_splitter = True
            total = self.splitter.width()
            # Start wide enough to read the panel without scrolling - the
            # install commands are long - but never take more than half.
            wanted = self.splitter.widget(0).widget().sizeHint().width() + 28
            panel = min(max(340, wanted), max(340, total // 2))
            self.splitter.setSizes([panel, total - panel])

    def _build_prompt_group(self):
        box = QGroupBox("Describe the image")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "A knight in weathered gold armour standing in heavy fog, "
            "dramatic rim lighting, highly detailed"
        )
        self.prompt_edit.setFixedHeight(90)
        layout.addWidget(self.prompt_edit)

        negative_label = QLabel("Things to avoid (optional)")
        negative_label.setObjectName("HintLabel")
        layout.addWidget(negative_label)
        self.negative_edit = QPlainTextEdit()
        self.negative_edit.setPlaceholderText("blurry, extra fingers, watermark, text")
        self.negative_edit.setFixedHeight(54)
        layout.addWidget(self.negative_edit)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.generate_button = QPushButton("Generate")
        self.generate_button.setObjectName("ConvertButton")
        self.generate_button.setCursor(Qt.PointingHandCursor)
        self.generate_button.setMinimumHeight(40)
        self.generate_button.clicked.connect(self.start_generation)
        row.addWidget(self.generate_button, 2)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.clicked.connect(self.cancel_generation)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button, 1)
        layout.addLayout(row)

        self.install_help = QLabel(generation_install_help())
        self.install_help.setObjectName("HintLabel")
        self.install_help.setWordWrap(True)
        self.install_help.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.install_help.setVisible(not GENERATION_AVAILABLE)
        layout.addWidget(self.install_help)

        self.install_row = QWidget()
        install_row = QHBoxLayout(self.install_row)
        install_row.setContentsMargins(0, 0, 0, 0)
        install_row.setSpacing(8)
        copy_button = QPushButton("Copy install command for this Python")
        copy_button.setObjectName("ChipButton")
        copy_button.setCursor(Qt.PointingHandCursor)
        copy_button.clicked.connect(self.copy_install_command)
        install_row.addWidget(copy_button)
        open_button = QPushButton("Open PyTorch's picker")
        open_button.setObjectName("ChipButton")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(TORCH_SELECTOR_URL))
        )
        install_row.addWidget(open_button)
        recheck_button = QPushButton("Check again")
        recheck_button.setObjectName("ChipButton")
        recheck_button.setCursor(Qt.PointingHandCursor)
        recheck_button.clicked.connect(self.recheck_backend)
        install_row.addWidget(recheck_button)
        diagnostics_button = QPushButton("Why isn't it working?")
        diagnostics_button.setObjectName("ChipButton")
        diagnostics_button.setCursor(Qt.PointingHandCursor)
        diagnostics_button.clicked.connect(self.show_diagnostics)
        install_row.addWidget(diagnostics_button)
        install_row.addStretch()
        self.install_row.setVisible(not GENERATION_AVAILABLE)
        layout.addWidget(self.install_row)
        return box

    def copy_install_command(self):
        QApplication.clipboard().setText(generation_install_command())
        self.status_label.setText(
            "Copied — paste it into Command Prompt. It installs into the "
            "Python this app is using."
        )

    def show_diagnostics(self):
        box = QMessageBox(self)
        box.setWindowTitle("Image generation diagnostics")
        box.setIcon(QMessageBox.Information)
        box.setText(
            "Here's what the app can see. The usual reason pip says "
            "\"already satisfied\" while the app disagrees is that the "
            "packages went into a different Python."
        )
        box.setDetailedText(generation_diagnostics())
        copy_button = box.addButton("Copy details", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is copy_button:
            QApplication.clipboard().setText(generation_diagnostics())

    def recheck_backend(self):
        if refresh_generation_backend():
            self.install_help.setVisible(False)
            self.install_row.setVisible(False)
            self.result_label.setText("Describe what you want and press Generate.")
            self.status_label.setText(f"Ready — runs on: {generation_device_description()}")
        else:
            missing = []
            if not TORCH_AVAILABLE:
                missing.append("PyTorch")
            if not DIFFUSERS_AVAILABLE:
                missing.append("diffusers")
            self.install_help.setText(generation_install_help())
            broken = TORCH_IMPORT_ERROR or DIFFUSERS_IMPORT_ERROR
            if broken and "No module named" not in broken:
                # Installed, but it won't load - a different problem entirely.
                self.status_label.setText(
                    f"Installed but failing to load: {broken[:80]} — "
                    "press \"Why isn't it working?\" for the details."
                )
            else:
                self.status_label.setText("Still missing: " + " and ".join(missing))
        self._update_controls()

    def _build_reference_group(self):
        box = QGroupBox("Reference images (optional)")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.reference_list = ReferenceList()
        layout.addWidget(self.reference_list)

        row = QHBoxLayout()
        row.setSpacing(8)
        add_button = QPushButton("Add…")
        add_button.setObjectName("ChipButton")
        add_button.setCursor(Qt.PointingHandCursor)
        add_button.clicked.connect(self.add_references)
        row.addWidget(add_button)
        remove_button = QPushButton("Remove")
        remove_button.setObjectName("ChipButton")
        remove_button.setCursor(Qt.PointingHandCursor)
        remove_button.clicked.connect(self.reference_list.remove_selected)
        row.addWidget(remove_button)
        clear_button = QPushButton("Clear")
        clear_button.setObjectName("ChipButton")
        clear_button.setCursor(Qt.PointingHandCursor)
        clear_button.clicked.connect(self.reference_list.clear_all)
        row.addWidget(clear_button)
        row.addStretch()
        layout.addLayout(row)

        strength_row = QHBoxLayout()
        strength_row.setSpacing(10)
        strength_row.addWidget(QLabel("How much to change"))
        self.strength_slider = QSlider(Qt.Horizontal)
        self.strength_slider.setRange(10, 100)
        self.strength_slider.setValue(65)
        self.strength_slider.setToolTip(
            "Low: stays close to the reference. High: treats it as a loose "
            "starting point and follows the description more."
        )
        strength_row.addWidget(self.strength_slider, 1)
        self.strength_label = QLabel("65%")
        self.strength_label.setMinimumWidth(44)
        self.strength_label.setAlignment(Qt.AlignCenter)
        strength_row.addWidget(self.strength_label)
        self.strength_slider.valueChanged.connect(
            lambda value: self.strength_label.setText(f"{value}%")
        )
        layout.addLayout(strength_row)

        hint = QLabel(
            "Drop images here or use Add. Several references are cropped to "
            "the output shape and averaged into one starting picture, so they "
            "blend together. Leave this empty to work from the description alone."
        )
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return box

    def _build_settings_group(self):
        """Settings fold away behind a header so they don't crowd the
        panel. The header carries a badge saying whether a token is still
        needed, which is the one thing most people have to come here for."""
        section = QWidget()
        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self.settings_header = QPushButton()
        self.settings_header.setCheckable(True)
        self.settings_header.setCursor(Qt.PointingHandCursor)
        self.settings_header.setMinimumHeight(44)
        self.settings_header.setToolTip("Model, size and your Hugging Face token")
        self.settings_header.setStyleSheet(
            "QPushButton { text-align: left; padding: 10px 14px; "
            "font-weight: bold; font-size: 14px; border-radius: 10px; "
            "border: 2px solid #9d7bff; background-color: rgba(157,123,255,0.14); }"
            "QPushButton:hover { background-color: rgba(157,123,255,0.26); }"
        )
        self.settings_header.toggled.connect(self._toggle_settings)
        outer.addWidget(self.settings_header)

        box = QFrame()
        box.setObjectName("ImageCard")
        self.settings_body = box
        body = QVBoxLayout(box)
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(14)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        body.addLayout(grid)

        grid.addWidget(QLabel("Model"), 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.addItems(list(GENERATION_MODELS))
        self.model_combo.setToolTip(
            "Downloaded once, then kept on this PC and used offline."
        )
        # Without this the longest entry would decide the panel's width.
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(16)
        self.model_combo.currentTextChanged.connect(self._model_changed)
        grid.addWidget(self.model_combo, 0, 1, 1, 3)

        self.custom_model_edit = QLineEdit()
        self.custom_model_edit.setPlaceholderText(
            "e.g. black-forest-labs/FLUX.1-schnell, or a folder on this PC"
        )
        self.custom_model_edit.setVisible(False)
        grid.addWidget(self.custom_model_edit, 1, 0, 1, 4)

        self.model_note = QLabel("")
        self.model_note.setObjectName("HintLabel")
        self.model_note.setWordWrap(True)
        grid.addWidget(self.model_note, 5, 0, 1, 4)

        grid.addWidget(QLabel("Size"), 2, 0)
        self.size_combo = QComboBox()
        self.size_combo.addItems(GENERATION_SIZES)
        grid.addWidget(self.size_combo, 2, 1, 1, 3)

        grid.addWidget(QLabel("Steps"), 3, 0)
        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 100)
        self.steps_spin.setValue(25)
        self.steps_spin.setToolTip("More steps: slower, usually a bit cleaner.")
        grid.addWidget(self.steps_spin, 3, 1)

        grid.addWidget(QLabel("Guidance"), 3, 2)
        self.guidance_spin = QSpinBox()
        self.guidance_spin.setRange(0, 20)
        self.guidance_spin.setValue(7)
        self.guidance_spin.setToolTip(
            "How strictly to follow the description. Turbo models want 0-2."
        )
        grid.addWidget(self.guidance_spin, 3, 3)

        grid.addWidget(QLabel("Seed"), 4, 0)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(-1, 2_147_483_647)
        self.seed_spin.setValue(-1)
        self.seed_spin.setToolTip("-1 picks a new random seed each time.")
        grid.addWidget(self.seed_spin, 4, 1)
        self.reuse_seed_button = QPushButton("Reuse last")
        self.reuse_seed_button.setObjectName("ChipButton")
        self.reuse_seed_button.setCursor(Qt.PointingHandCursor)
        self.reuse_seed_button.setToolTip(
            "Puts the last result's seed back, so you can tweak the wording "
            "and get a similar picture."
        )
        self.reuse_seed_button.clicked.connect(self.reuse_last_seed)
        self.reuse_seed_button.setEnabled(False)
        grid.addWidget(self.reuse_seed_button, 4, 2, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        # The token gets its own highlighted how-to box, since it's the step
        # people miss.
        token_box = QFrame()
        token_box.setObjectName("TokenBox")
        token_box.setStyleSheet(
            "QFrame#TokenBox { border: 2px solid #9d7bff; border-radius: 10px; "
            "background-color: rgba(157,123,255,0.10); }"
            "QFrame#TokenBox QLabel { background: transparent; border: none; }"
        )
        token_layout = QVBoxLayout(token_box)
        token_layout.setContentsMargins(14, 12, 14, 12)
        token_layout.setSpacing(8)
        token_title = QLabel("\U0001F511  <b>Hugging Face token</b>")
        token_title.setTextFormat(Qt.RichText)
        token_layout.addWidget(token_title)
        token_hint = QLabel(
            "FLUX.1 and SD 3.5 won't download without one. It's free and "
            "takes a couple of minutes, once: press \"How to get a token\" "
            "for the steps, paste it here and press Remember."
        )
        token_hint.setWordWrap(True)
        token_layout.addWidget(token_hint)

        token_row = QHBoxLayout()
        token_row.setSpacing(8)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("paste your token here (hf_…)")
        self.token_edit.setToolTip(
            "Some models are behind a licence you accept on their page, and "
            "then need an access token to download.\n"
            "Press \"How to get a token\" for the steps."
        )
        self.token_edit.setText(load_remembered_hf_token())
        set_session_hf_token(self.token_edit.text())
        self.token_edit.textChanged.connect(self._token_edited)
        token_row.addWidget(self.token_edit, 1)
        self.remember_token_button = QPushButton("Remember")
        self.remember_token_button.setObjectName("ChipButton")
        self.remember_token_button.setCursor(Qt.PointingHandCursor)
        self.remember_token_button.clicked.connect(self._remember_clicked)
        token_row.addWidget(self.remember_token_button)
        token_layout.addLayout(token_row)

        self.token_help_button = QPushButton("\u24d8  How to get a token")
        self.token_help_button.setObjectName("ConvertButton")
        self.token_help_button.setCursor(Qt.PointingHandCursor)
        self.token_help_button.setToolTip(
            "Step-by-step help for getting the free token FLUX.1 needs.")
        self.token_help_button.clicked.connect(self.show_token_guide)
        token_layout.addWidget(self.token_help_button)
        body.addWidget(token_box)
        outer.addWidget(box)

        self._model_changed(self.model_combo.currentText())
        self._update_remember_button()
        # Start folded away, unless there's no token yet - then open, so
        # the how-to box is the first thing seen.
        self.settings_header.setChecked(not load_remembered_hf_token())
        self._toggle_settings(self.settings_header.isChecked())
        return section

    def _toggle_settings(self, open_):
        self.settings_body.setVisible(open_)
        self._update_settings_header()

    def _update_settings_header(self):
        arrow = "\u25BE" if self.settings_header.isChecked() else "\u25B8"
        if huggingface_token():
            badge = "\u2714 token set"
        else:
            badge = "\u26A0 token needed for FLUX.1"
        action = "hide" if self.settings_header.isChecked() else "click to open"
        self.settings_header.setText(
            f"{arrow}   \u2699  Settings   \u00b7   {badge}   ({action})")

    def _token_edited(self, text):
        set_session_hf_token(text)
        self._update_remember_button()
        self._update_settings_header()

    def _update_remember_button(self):
        token = self.token_edit.text().strip()
        remembered = bool(token) and token == load_remembered_hf_token()
        self.remember_token_button.setText(
            "Forget" if remembered else "Remember")
        self.remember_token_button.setEnabled(bool(token) or remembered)
        self.remember_token_button.setToolTip(
            "Deletes the saved token from this PC." if remembered else
            "Saves the token on this PC so it's filled in next time.")

    def _remember_clicked(self):
        if self.remember_token_button.text() == "Forget":
            forget_hf_token()
            self.token_edit.clear()
            self.status_label.setText("Token forgotten.")
            self._update_remember_button()
        else:
            self.remember_token()

    def remember_token(self):
        """Saves the token in the box to a file. True if it worked."""
        token = self.token_edit.text().strip()
        if not token:
            return False
        try:
            remember_hf_token(token)
        except OSError as exc:
            show_error(self, "Couldn't save the token",
                       f"The token couldn't be written to {hf_token_path()}."
                       f"\n\n{exc}")
            return False
        set_session_hf_token(token)
        self._update_remember_button()
        self.status_label.setText("Token remembered for next time.")
        return True

    def show_token_guide(self):
        model_id, _family = self._selected_model()
        if not model_id or os.path.isdir(model_id):
            model_id = "black-forest-labs/FLUX.1-schnell"
        HFTokenGuideDialog(self, model_id).exec()

    def _model_changed(self, label):
        model = GENERATION_MODELS[label]
        self.custom_model_edit.setVisible(model["id"] == "")
        self.model_note.setText(model.get("note", ""))
        # Each model has its own sensible starting point - turbo and FLUX
        # schnell want very few steps and little or no guidance.
        self.steps_spin.setValue(model["steps"])
        self.guidance_spin.setValue(model["guidance"])
        for index, text in enumerate(GENERATION_SIZES):
            if text.startswith(f"{model['size']} × {model['size']}"):
                self.size_combo.setCurrentIndex(index)
                break

    def _selected_model(self):
        """(model id, family) for whatever is picked, custom included."""
        model = GENERATION_MODELS[self.model_combo.currentText()]
        model_id = model["id"] or self.custom_model_edit.text().strip()
        return model_id, model["family"]

    # -- helpers ---------------------------------------------------------------

    def _selected_size(self):
        text = self.size_combo.currentText().split("(")[0]
        width, height = text.replace("×", "x").split("x")
        return int(width.strip()), int(height.strip())

    def _update_controls(self):
        can_generate = GENERATION_AVAILABLE and not self.busy
        self.generate_button.setEnabled(can_generate)
        self.cancel_button.setEnabled(self.busy)
        for widget in (self.save_button, self.use_as_reference_button,
                       self.send_to_remover_button):
            widget.setEnabled(self.result is not None and not self.busy)

    def add_references(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add reference images", "", WEB_IMAGE_FILTER
        )
        for path in paths:
            self.reference_list.add_path(path)

    # -- generating ------------------------------------------------------------

    def start_generation(self):
        if self.busy or not GENERATION_AVAILABLE:
            return
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            QMessageBox.information(
                self, "Describe the image first",
                "Type a description of the picture you'd like."
            )
            return

        size = self._selected_size()
        references = self.reference_list.images()
        seed = self.seed_spin.value()
        if seed < 0:
            seed = int.from_bytes(os.urandom(4), "big") % 2_147_483_647

        model_id, family = self._selected_model()
        if not model_id:
            QMessageBox.information(
                self, "Which model?",
                "Type the name of a Hugging Face model, or the folder it's in."
            )
            return

        request = {
            "model_id": model_id,
            "family": family,
            "prompt": prompt,
            "negative_prompt": self.negative_edit.toPlainText().strip(),
            "steps": self.steps_spin.value(),
            "guidance": float(self.guidance_spin.value()),
            "size": size,
            "seed": seed,
            "strength": self.strength_slider.value() / 100,
            "init_image": blend_reference_images(references, size),
        }

        self.busy = True
        self._update_controls()
        self.progress.setRange(0, request["steps"])
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.status_label.setText("Starting…")

        self.worker = GenerationWorker(request)
        self.worker.signals.progress.connect(self._on_progress)
        self.worker.signals.status.connect(self.status_label.setText)
        self.worker.signals.finished.connect(self._on_finished)
        self.worker.signals.failed.connect(self._on_failed)
        self.worker.signals.cancelled.connect(self._on_cancelled)
        self.main_window.thread_pool.start(self.worker)

    def cancel_generation(self):
        if self.worker is not None:
            self.worker.cancel()
            self.status_label.setText("Stopping after this step…")

    def _on_progress(self, step, total):
        self.progress.setRange(0, total)
        self.progress.setValue(step)
        self.status_label.setText(f"Generating… step {step} of {total}")

    def _finish(self):
        self.busy = False
        self.worker = None
        self.progress.setVisible(False)
        self._update_controls()

    def _on_finished(self, image, seed):
        self.result = image.convert("RGBA")
        self.last_seed = seed
        self.reuse_seed_button.setEnabled(True)
        self._finish()
        self.show_result()
        self.status_label.setText(
            f"Done — {self.result.width} × {self.result.height}, seed {seed}."
        )

    def _on_failed(self, error_message):
        self._finish()
        self.status_label.setText("Generation failed.")
        model_id, _family = self._selected_model()
        show_error(self, "Generation failed",
                   explain_model_error(error_message, model_id),
                   details=error_message)

    def _on_cancelled(self):
        self._finish()
        self.status_label.setText("Stopped.")

    def show_result(self):
        if self.result is None:
            return
        preview = self.result.copy()
        preview.thumbnail((self.result_label.width() - 20,
                           self.result_label.height() - 20), RESAMPLE_LANCZOS)
        self.result_label.setPixmap(QPixmap.fromImage(pil_to_qimage(preview)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.show_result()

    def reuse_last_seed(self):
        if getattr(self, "last_seed", None) is not None:
            self.seed_spin.setValue(int(self.last_seed))

    # -- what to do with the result --------------------------------------------

    def save_result(self):
        if self.result is None:
            return
        default = os.path.join(os.path.expanduser("~"), "generated.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save image", default, "PNG Image (*.png);;WebP Image (*.webp)"
        )
        if not path:
            return
        fmt = "webp" if path.lower().endswith(".webp") else "png"
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        try:
            _web_save_still(self.result, path, fmt)
        except Exception as exc:  # noqa: BLE001
            show_critical(self, "Save failed", str(exc))
            return
        self.status_label.setText(f"Saved {os.path.basename(path)}")

    def _result_to_temp_file(self):
        folder = os.path.join(tempfile.gettempdir(), "imagegen_generated")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"generated_{int(datetime.now().timestamp())}.png")
        self.result.save(path, "PNG")
        return path

    def use_result_as_reference(self):
        if self.result is None:
            return
        self.reference_list.add_path(self._result_to_temp_file())
        self.status_label.setText("Added the result to the references.")

    def send_result_to_remover(self):
        if self.result is None:
            return
        remover = getattr(self.main_window, "background_tab", None)
        if remover is None or not hasattr(remover, "load_image_from_path"):
            QMessageBox.information(
                self, "Background Remover unavailable",
                "That tab needs NumPy installed to work."
            )
            return
        remover.load_image_from_path(self._result_to_temp_file())
        self.main_window.show_tab(remover)


class EdgeResizeGrip(QWidget):
    """An invisible strip along one edge or corner of a frameless window
    that resizes it when dragged.

    A window with no system frame gets no resize borders, which leaves only
    the little corner grip to drag. These put the normal behaviour back on
    all four sides and all four corners.
    """

    THICKNESS = 6

    def __init__(self, window, left=False, right=False, top=False, bottom=False):
        super().__init__(window)
        self.window_ref = window
        self.left, self.right = left, right
        self.top, self.bottom = top, bottom
        self._start_pos = None
        self._start_geometry = None

        vertical = (top or bottom) and not (left or right)
        horizontal = (left or right) and not (top or bottom)
        if vertical:
            cursor = Qt.SizeVerCursor
        elif horizontal:
            cursor = Qt.SizeHorCursor
        elif (top and left) or (bottom and right):
            cursor = Qt.SizeFDiagCursor
        else:
            cursor = Qt.SizeBDiagCursor
        self.setCursor(cursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._start_pos = event.globalPosition().toPoint()
            self._start_geometry = self.window_ref.geometry()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._start_pos is None:
            return
        delta = event.globalPosition().toPoint() - self._start_pos
        geometry = QRect(self._start_geometry)
        minimum = self.window_ref.minimumSize()

        if self.left:
            new_left = min(geometry.left() + delta.x(),
                           geometry.right() - minimum.width())
            geometry.setLeft(new_left)
        if self.right:
            geometry.setRight(max(geometry.right() + delta.x(),
                                  geometry.left() + minimum.width()))
        if self.top:
            new_top = min(geometry.top() + delta.y(),
                          geometry.bottom() - minimum.height())
            geometry.setTop(new_top)
        if self.bottom:
            geometry.setBottom(max(geometry.bottom() + delta.y(),
                                   geometry.top() + minimum.height()))

        self.window_ref.setGeometry(geometry)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._start_pos = None
        self._start_geometry = None


class WindowResizeGrips:
    """Puts an EdgeResizeGrip on every edge and corner of a window and keeps
    them in place as it changes size."""

    def __init__(self, window):
        self.window = window
        thickness = EdgeResizeGrip.THICKNESS
        self.thickness = thickness
        self.grips = {
            "left": EdgeResizeGrip(window, left=True),
            "right": EdgeResizeGrip(window, right=True),
            "top": EdgeResizeGrip(window, top=True),
            "bottom": EdgeResizeGrip(window, bottom=True),
            "top_left": EdgeResizeGrip(window, top=True, left=True),
            "top_right": EdgeResizeGrip(window, top=True, right=True),
            "bottom_left": EdgeResizeGrip(window, bottom=True, left=True),
            "bottom_right": EdgeResizeGrip(window, bottom=True, right=True),
        }

    def reposition(self):
        """Lays the grips out, and hides them while maximised - a maximised
        window isn't resizable by dragging."""
        width = self.window.width()
        height = self.window.height()
        t = self.thickness
        visible = not self.window.is_maximised()

        places = {
            "left": (0, t, t, max(0, height - 2 * t)),
            "right": (width - t, t, t, max(0, height - 2 * t)),
            "top": (t, 0, max(0, width - 2 * t), t),
            "bottom": (t, height - t, max(0, width - 2 * t), t),
            "top_left": (0, 0, t, t),
            "top_right": (width - t, 0, t, t),
            "bottom_left": (0, height - t, t, t),
            "bottom_right": (width - t, height - t, t, t),
        }
        for name, grip in self.grips.items():
            grip.setGeometry(*places[name])
            grip.setVisible(visible)
            if visible:
                grip.raise_()


# What each extension adds, what it's called on the GitHub release, and
# roughly how big it is. Keep the ids the same as the installer's.
EXTENSION_CATALOGUE = {
    "web_images": {
        "name": "Web Images",
        "asset": "extension_web_images.zip",
        "size_mb": 180,
        "description": "Built-in browser for collecting images from a page, "
                       "plus the Overlay Studio.",
    },
    "video_tools": {
        "name": "Video tools",
        "asset": "extension_video.zip",
        "size_mb": 90,
        "description": "Video to GIF and Video to Image.",
    },
    "flipbook": {
        "name": "WebP Flipbook",
        "asset": "extension_flipbook.zip",
        "size_mb": 15,
        "description": "Sprite sheets and animated WebP to GIF.",
    },
    "background_remover": {
        "name": "Background Remover",
        "asset": "extension_bgremove.zip",
        "size_mb": 60,
        "description": "AI cutouts, magic wand and brushes.",
    },
    "image_creation": {
        "name": "Image Creation",
        "asset": "extension_imagegen.zip",
        "size_mb": 2600,
        "description": "Generate images locally from a description. The big "
                       "one - it brings in PyTorch.",
    },
}


GITHUB_RELEASES_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
)


class ExtensionNotPublished(Exception):
    """No release on GitHub carries this extension's .zip yet."""


# How to tell each tool already works without its extension - true when the
# .exe was built with that tool's libraries inside it.
EXTENSION_BUILT_IN = {
    "web_images": lambda: WEBENGINE_AVAILABLE,
    "video_tools": lambda: MOVIEPY_AVAILABLE,
    "flipbook": lambda: NUMPY_AVAILABLE,
    "background_remover": lambda: ONNX_AVAILABLE,
    "image_creation": lambda: GENERATION_AVAILABLE,
}


def extension_asset_url(asset):
    """Where to download an extension from.

    Looks through the recent releases, newest first, for one that actually
    has the file attached. Going straight to releases/latest 404s whenever
    the newest release was published without its extensions, even though an
    older release has them.
    """
    request = urllib.request.Request(
        f"{GITHUB_RELEASES_API}?per_page=20",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"{APP_TITLE}-extensions"})
    with urllib.request.urlopen(request, timeout=20) as response:
        releases = json.loads(response.read().decode("utf-8"))
    for release in releases:
        if release.get("draft"):
            continue
        for item in release.get("assets", []):
            if item.get("name") == asset and item.get("browser_download_url"):
                return item["browser_download_url"]
    raise ExtensionNotPublished(
        f"{asset} isn't attached to any release on "
        f"github.com/{GITHUB_OWNER}/{GITHUB_REPO} yet, so there's nothing to "
        f"download. It needs building (build_extensions.py) and uploading to "
        f"a release first.")


class ExtensionSignals(QObject):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(str, str)   # extension id, message
    failed = pyqtSignal(str, str)


class ExtensionInstallWorker(QRunnable):
    """Downloads one extension and unpacks it into extensions/<id>/."""

    def __init__(self, extension_id, asset):
        super().__init__()
        self.extension_id = extension_id
        self.asset = asset
        self.signals = ExtensionSignals()

    def run(self):
        destination = os.path.join(extensions_folder(), self.extension_id)
        archive = destination + ".part"
        try:
            os.makedirs(extensions_folder(), exist_ok=True)
            self.signals.status.emit("Finding the download…")
            url = extension_asset_url(self.asset)
            self.signals.status.emit("Downloading…")
            request = urllib.request.Request(
                url,
                headers={"User-Agent": f"{APP_TITLE}-extensions"})
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                with open(archive, "wb") as handle:
                    while True:
                        chunk = response.read(262144)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        if total:
                            self.signals.progress.emit(int(done / total * 95))

            self.signals.status.emit("Unpacking…")
            if os.path.isdir(destination):
                shutil.rmtree(destination, ignore_errors=True)
            os.makedirs(destination, exist_ok=True)
            shutil.unpack_archive(archive, destination, format="zip")
            os.remove(archive)
            self.signals.progress.emit(100)
            self.signals.finished.emit(
                self.extension_id,
                "Installed. Restart the app to start using it.")
        except Exception as exc:  # noqa: BLE001
            for leftover in (archive,):
                if os.path.exists(leftover):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
            if isinstance(exc, ExtensionNotPublished):
                message = str(exc)
            else:
                message = (f"{type(exc).__name__}: {exc}\n\n"
                           f"{traceback.format_exc().rstrip()}")
            self.signals.failed.emit(self.extension_id, message)


class ExtensionRow(QFrame):
    """One extension in the manager."""

    def __init__(self, extension_id, info, manager):
        super().__init__()
        self.extension_id = extension_id
        self.info = info
        self.manager = manager
        self.setObjectName("ImageCard")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        column = QVBoxLayout()
        column.setSpacing(3)
        size = (f"{info['size_mb'] / 1024:.1f} GB" if info["size_mb"] >= 1024
                else f"{info['size_mb']} MB")
        self.title_label = QLabel(f"<b>{info['name']}</b>  ·  {size}")
        self.title_label.setTextFormat(Qt.RichText)
        column.addWidget(self.title_label)
        description = QLabel(info["description"])
        description.setObjectName("HintLabel")
        description.setWordWrap(True)
        column.addWidget(description)
        self.status_label = QLabel("")
        self.status_label.setObjectName("HintLabel")
        column.addWidget(self.status_label)
        layout.addLayout(column, 1)

        self.progress = QProgressBar()
        self.progress.setObjectName("NiceProgressBar")
        self.progress.setFixedWidth(130)
        self.progress.setFixedHeight(10)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.action_button = QPushButton("")
        self.action_button.setCursor(Qt.PointingHandCursor)
        self.action_button.clicked.connect(self.on_action)
        layout.addWidget(self.action_button)
        self.refresh()

    def is_installed(self):
        return self.extension_id in installed_extension_ids()

    def is_built_in(self):
        """The tool's libraries are part of this .exe, so it works without
        the extension being downloaded."""
        return (not self.is_installed()
                and EXTENSION_BUILT_IN.get(self.extension_id, lambda: False)())

    def refresh(self):
        installed = self.is_installed()
        built_in = self.is_built_in()
        self.action_button.setText("Remove" if installed else "Install")
        self.action_button.setObjectName(
            "" if installed or built_in else "ConvertButton")
        self.action_button.setStyleSheet(self.action_button.styleSheet())
        self.action_button.setVisible(not built_in)
        if built_in:
            self.status_label.setText("Built into this version")
        else:
            self.status_label.setText(
                "Installed" if installed else "Not installed")

    def on_action(self):
        if self.is_installed():
            self.remove()
        else:
            self.install()

    def install(self):
        self.action_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        worker = ExtensionInstallWorker(self.extension_id, self.info["asset"])
        worker.signals.progress.connect(self.progress.setValue)
        worker.signals.status.connect(self.status_label.setText)
        worker.signals.finished.connect(self.on_finished)
        worker.signals.failed.connect(self.on_failed)
        self.manager.main_window.thread_pool.start(worker)

    def on_finished(self, _extension_id, message):
        self.progress.setVisible(False)
        self.action_button.setEnabled(True)
        self.refresh()
        self.status_label.setText(message)
        self.manager.note_restart_needed()

    def on_failed(self, _extension_id, message):
        self.progress.setVisible(False)
        self.action_button.setEnabled(True)
        self.refresh()
        self.status_label.setText("Couldn't install it.")
        summary, _, details = message.partition("\n\n")
        show_error(
            self, "Extension install failed",
            f"{self.info['name']} didn't install.\n\n{summary}",
            details=details,
        )

    def remove(self):
        answer = QMessageBox.question(
            self, f"Remove {self.info['name']}?",
            f"This deletes the extension's files. {self.info['name']} will be "
            f"greyed out until you install it again.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        shutil.rmtree(os.path.join(extensions_folder(), self.extension_id),
                      ignore_errors=True)
        self.refresh()
        self.status_label.setText("Removed. Restart to finish.")
        self.manager.note_restart_needed()


class ExtensionsDialog(QDialog):
    """Lists every extension, installed or not, and installs on demand."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Extensions")
        self.setMinimumSize(640, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        heading = QLabel("<b>Extensions</b>")
        heading.setTextFormat(Qt.RichText)
        layout.addWidget(heading)
        intro = QLabel(
            "Tools are downloaded separately, which keeps the app itself "
            "small. Install one here and it switches on next time you start "
            "the app; remove one and it's greyed out again."
        )
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 8, 0)
        holder_layout.setSpacing(10)
        self.rows = []
        for extension_id, info in EXTENSION_CATALOGUE.items():
            row = ExtensionRow(extension_id, info, self)
            self.rows.append(row)
            holder_layout.addWidget(row)
        holder_layout.addStretch()
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)

        self.restart_label = QLabel("")
        self.restart_label.setObjectName("MutedLabel")
        self.restart_label.setWordWrap(True)
        layout.addWidget(self.restart_label)

        buttons = QHBoxLayout()
        folder_button = QPushButton("Open extensions folder")
        folder_button.setCursor(Qt.PointingHandCursor)
        folder_button.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(extensions_folder())))
        buttons.addWidget(folder_button)
        buttons.addStretch()
        close_button = QPushButton("Close")
        close_button.setObjectName("ConvertButton")
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def note_restart_needed(self):
        self.restart_label.setText(
            "Restart ImageGen to apply the change."
        )


class HomeTile(QFrame):
    """One tool on the home screen."""

    def __init__(self, title, description, available, on_open):
        super().__init__()
        self.setObjectName("ImageCard")
        self.setCursor(Qt.PointingHandCursor if available else Qt.ArrowCursor)
        self.on_open = on_open
        self.available = available
        self.setMinimumHeight(118)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        heading = QLabel(title)
        heading.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(heading)

        body = QLabel(description)
        body.setObjectName("HintLabel")
        body.setWordWrap(True)
        layout.addWidget(body, 1)

        if not available:
            note = QLabel("Not installed — click to add it")
            note.setObjectName("MutedLabel")
            layout.addWidget(note)
            self.setEnabled(True)
            heading.setStyleSheet("font-size: 15px; font-weight: 700; color: #8a8a8e;")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.on_open()


class HomeTab(QWidget):
    """The screen the app opens on: a tile per tool, rather than dropping
    straight into whichever tab happened to be first."""

    TOOLS = [
        ("Images", "Convert and resize images in batches."),
        ("Video to GIF", "Turn a clip into a GIF."),
        ("GIF Optimiser", "Shrink a GIF to a target size."),
        ("Image Optimiser", "Shrink an image to a target size."),
        ("WebP to GIF", "Convert WebP files, animated or still."),
        ("Video to Image", "Pull a single frame out of a video."),
        ("Web Images", "Collect images from any webpage."),
        ("WebP Flipbook", "Sprite sheets and animated WebP to GIF."),
        ("Background Remover", "Cut out people and characters."),
        ("Image Creation", "Generate images from a description."),
    ]

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 20, 26, 20)
        root.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(14)
        logo_path = logo_png_path()
        if logo_path:
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                logo = QLabel()
                logo.setPixmap(pixmap.scaledToHeight(52, Qt.SmoothTransformation))
                header.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        name = QLabel(f"<b style='font-size:24px;'>{APP_TITLE}</b>")
        name.setTextFormat(Qt.RichText)
        titles.addWidget(name)
        tagline = QLabel("Pick a tool to get started.")
        tagline.setObjectName("MutedLabel")
        titles.addWidget(tagline)
        header.addLayout(titles)
        header.addStretch()
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        self.holder = holder
        self.tiles = []
        self.rebuild()

    def rebuild(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.tiles = []
        missing = getattr(self.main_window, "missing_components", set())
        for index, (title, description) in enumerate(self.TOOLS):
            available = title not in missing
            tile = HomeTile(title, description, available,
                            lambda t=title: self.main_window.open_tool(t))
            self.grid.addWidget(tile, index // 3, index % 3)
            self.tiles.append(tile)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-flow the tiles so they stay readable at any width.
        columns = max(1, min(4, self.width() // 300))
        if columns != getattr(self, "_columns", None):
            self._columns = columns
            for index, tile in enumerate(self.tiles):
                self.grid.addWidget(tile, index // columns, index % columns)


class SettingsDialog(QDialog):
    """Everything that used to hide in the Options dropdown, in one window
    with Apply and Save."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.settings = main_window.settings
        self.setWindowTitle("Settings")
        self.setMinimumSize(520, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        appearance = QGroupBox("Appearance")
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(16, 16, 16, 16)
        appearance_layout.setSpacing(10)
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        self.theme_combo.setCurrentText(main_window.theme.capitalize())
        theme_row.addWidget(self.theme_combo, 1)
        appearance_layout.addLayout(theme_row)
        self.home_check = QCheckBox("Open on the Home screen")
        self.home_check.setChecked(
            str(self.settings.value("start_on_home", "true")).lower() == "true")
        appearance_layout.addWidget(self.home_check)
        layout.addWidget(appearance)

        behaviour = QGroupBox("Behaviour")
        behaviour_layout = QVBoxLayout(behaviour)
        behaviour_layout.setContentsMargins(16, 16, 16, 16)
        behaviour_layout.setSpacing(10)
        self.filenames_check = QCheckBox("Show full file names in lists")
        self.filenames_check.setChecked(
            str(self.settings.value("show_filenames", "false")).lower() == "true")
        behaviour_layout.addWidget(self.filenames_check)
        self.updates_check = QCheckBox("Check for updates when the app starts")
        self.updates_check.setChecked(
            str(self.settings.value("check_updates", "true")).lower() == "true")
        behaviour_layout.addWidget(self.updates_check)
        layout.addWidget(behaviour)

        tools = QGroupBox("Tools and updates")
        tools_layout = QVBoxLayout(tools)
        tools_layout.setContentsMargins(16, 16, 16, 16)
        tools_layout.setSpacing(10)
        extensions_button = QPushButton("Manage extensions…")
        extensions_button.setCursor(Qt.PointingHandCursor)
        extensions_button.clicked.connect(main_window.show_extensions)
        tools_layout.addWidget(extensions_button)
        updates_button = QPushButton("Check for updates now")
        updates_button.setCursor(Qt.PointingHandCursor)
        updates_button.clicked.connect(
            lambda: main_window.check_for_updates(silent=False))
        tools_layout.addWidget(updates_button)
        if DISCORD_URL:
            discord_button = QPushButton("Visit our Discord")
            discord_button.setCursor(Qt.PointingHandCursor)
            discord_button.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(DISCORD_URL)))
            tools_layout.addWidget(discord_button)
        layout.addWidget(tools)

        about = QLabel(f"{APP_TITLE} v{APP_VERSION}  ·  {MADE_WITH_TEXT}")
        about.setObjectName("MutedLabel")
        layout.addWidget(about)
        layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        apply_button = QPushButton("Apply")
        apply_button.setCursor(Qt.PointingHandCursor)
        apply_button.clicked.connect(self.apply_settings)
        buttons.addWidget(apply_button)
        save = QPushButton("Save")
        save.setObjectName("ConvertButton")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.save_settings)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def apply_settings(self):
        self.main_window.set_theme(self.theme_combo.currentText().lower())
        self.settings.setValue("show_filenames",
                               "true" if self.filenames_check.isChecked() else "false")
        self.settings.setValue("check_updates",
                               "true" if self.updates_check.isChecked() else "false")
        self.settings.setValue("start_on_home",
                               "true" if self.home_check.isChecked() else "false")

    def save_settings(self):
        self.apply_settings()
        self.accept()


class NotInstalledTab(QWidget):
    """Stands in for a tool whose component wasn't installed."""

    def __init__(self, component_id, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        name = COMPONENT_NAMES.get(component_id, component_id)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)

        box = QGroupBox(f"{name} isn't installed")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(18, 16, 18, 18)
        box_layout.setSpacing(12)
        message = QLabel(
            f"This tool wasn't ticked when the app was installed, so it isn't "
            f"on this PC yet.\n\n"
            f"Run the installer again and tick <b>{name}</b> to add it — your "
            f"other tools and settings are left alone."
        )
        message.setWordWrap(True)
        message.setTextFormat(Qt.RichText)
        box_layout.addWidget(message)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        get_it = QPushButton(f"Get {name}")
        get_it.setObjectName("ConvertButton")
        get_it.setCursor(Qt.PointingHandCursor)
        get_it.clicked.connect(main_window.show_extensions)
        buttons.addWidget(get_it)
        buttons.addStretch()
        box_layout.addLayout(buttons)
        box_layout.addStretch()
        layout.addWidget(box, 1)


class UpdateCheckSignals(QObject):
    update_available = pyqtSignal(dict)  # {"version": str, "url": str, "notes": str}
    no_update = pyqtSignal()
    failed = pyqtSignal(str)


class UpdateCheckWorker(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = UpdateCheckSignals()

    def run(self):
        try:
            request = urllib.request.Request(
                GITHUB_LATEST_RELEASE_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"{GITHUB_REPO}-update-check",
                },
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))

            latest_tag = data.get("tag_name", "")
            if not latest_tag:
                self.signals.no_update.emit()
                return

            if _parse_version(latest_tag) > _parse_version(APP_VERSION):
                # Find the app .exe among the release's files, so the
                # updater can fetch it directly instead of sending the
                # user to a web page.
                download_url = ""
                for asset in data.get("assets", []):
                    name = (asset.get("name") or "").lower()
                    if name.endswith(".exe") and "setup" not in name and \
                            "updater" not in name:
                        download_url = asset.get("browser_download_url", "")
                        break
                self.signals.update_available.emit({
                    "version": latest_tag,
                    "download_url": download_url,
                    "url": data.get(
                        "html_url",
                        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases",
                    ),
                    "notes": data.get("body", "") or "",
                })
            else:
                self.signals.no_update.emit()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.signals.failed.emit(
                    f"No releases found for {GITHUB_OWNER}/{GITHUB_REPO} yet."
                )
            else:
                self.signals.failed.emit(f"GitHub returned an error: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} (v{APP_VERSION})")
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.FramelessWindowHint)
        # Roomy by default, but never bigger than the screen it opens on.
        # 1000x600 is the size every tab needs to lay out without scrolling;
        # on a small or heavily-scaled display the minimum is pulled in to
        # whatever the screen can actually show, so the window always fits.
        available = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(min(1000, int(available.width() * 0.95)),
                            min(600, int(available.height() * 0.95)))
        self.resize(min(1220, int(available.width() * 0.92)),
                    min(840, int(available.height() * 0.90)))
        self._normal_geometry = None
        self._maximised = False
        self.resize_grips = None

        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(MAX_CONCURRENT_CONVERSIONS)

        # path -> QListWidgetItem, for files still waiting for a slot
        self.queued_items = {}
        # path -> {"item": QListWidgetItem, "widget": CompletedRowWidget}
        self.completed_rows = {}
        # path -> PIL Image, populated once conversion finishes in memory
        self.converted_images = {}
        # paths already handed to the thread pool, so Convert can be
        # clicked more than once without resubmitting the same file
        self.submitted_paths = set()
        # paths whose conversion ended in failure (still shown in
        # Completed until cleared)
        self.failed_paths = set()

        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        saved_theme = self.settings.value("theme", "light")
        if saved_theme not in ("light", "dark"):
            saved_theme = "light"

        self.theme = saved_theme
        self.delete_originals = False
        self.show_filenames_after_conversion = False
        self._filenames_window = None
        self.format_manually_set = False

        self._build_ui()
        # Re-apply now that the title bar (whose toggle label needs
        # updating) actually exists - _build_ui always starts from the
        # light stylesheet, so this is what makes a saved dark preference
        # take effect on launch.
        self.set_theme(self.theme)

        # Resize handles on every edge, since a frameless window has none.
        self.resize_grips = WindowResizeGrips(self)
        self.resize_grips.reposition()

        self.format_combo.activated.connect(self.on_format_manually_changed)

        # Check for updates a couple seconds after launch, quietly - only
        # pop up a dialog if there's actually something new.
        QTimer.singleShot(2000, lambda: self.check_for_updates(silent=True))

    def on_format_manually_changed(self, index):
        self.format_manually_set = True

    def set_delete_originals(self, enabled):
        self.delete_originals = enabled

    def set_show_filenames_after_conversion(self, enabled):
        self.show_filenames_after_conversion = enabled

    def show_filenames_window(self, filenames):
        """Pops open (or re-uses/re-populates) the small non-modal window
        listing output filenames, for copy-pasting elsewhere. Called after
        a successful save/conversion when the option is checked."""
        if not filenames:
            return
        if self._filenames_window is None:
            self._filenames_window = FilenamesWindow(self)
        self._filenames_window.set_filenames(filenames)
        self._filenames_window.show()
        self._filenames_window.raise_()
        self._filenames_window.activateWindow()

    def open_tool(self, title):
        """Used by the home screen to jump to a tool."""
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == title:
                self.on_pill_tab_clicked(index)
                return

    def show_settings(self):
        SettingsDialog(self).exec()

    def show_extensions(self):
        ExtensionsDialog(self).exec()

    def check_for_updates(self, silent=False):
        worker = UpdateCheckWorker()
        worker.signals.update_available.connect(self.on_update_available)
        worker.signals.no_update.connect(lambda: self.on_no_update(silent))
        worker.signals.failed.connect(lambda msg: self.on_update_check_failed(msg, silent))
        self.thread_pool.start(worker)

    UPDATER_NAMES = ("ImageGenUpdater.exe", "updater.py")

    def _app_folder(self):
        return os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__))

    def _find_updater(self):
        for name in self.UPDATER_NAMES:
            path = os.path.join(self._app_folder(), name)
            if os.path.exists(path):
                return path
        return None

    def on_update_available(self, info):
        version = info.get("version", "")
        if self.settings.value("skipped_update", "") == version:
            return  # the user asked not to be told about this one again

        dialog = QMessageBox(self)
        dialog.setWindowTitle("Update available")
        dialog.setIconPixmap(app_icon().pixmap(48, 48))
        dialog.setText(f"<b>{APP_TITLE} {version} is available.</b>")
        dialog.setInformativeText(
            f"You're on v{APP_VERSION}. The update downloads in the "
            f"background and takes a few seconds — the app closes and "
            f"reopens on its own."
        )
        notes = (info.get("notes") or "").strip()
        if notes:
            dialog.setDetailedText(notes)

        update_button = dialog.addButton("Update now", QMessageBox.AcceptRole)
        dialog.addButton("Not now", QMessageBox.RejectRole)
        skip_button = dialog.addButton("Skip this version", QMessageBox.DestructiveRole)
        dialog.setDefaultButton(update_button)
        dialog.exec()

        clicked = dialog.clickedButton()
        if clicked is skip_button:
            self.settings.setValue("skipped_update", version)
        elif clicked is update_button:
            self.start_update(info)
        # "Not now" needs no action - it's simply asked again next launch.

    def start_update(self, info):
        """Hands over to the separate updater and closes, because Windows
        won't let a running program overwrite its own .exe."""
        updater = self._find_updater()
        download = info.get("download_url") or ""
        target = sys.executable if getattr(sys, "frozen", False) else ""

        if not updater or not download or not target:
            # Nothing to hand off to (or running from source, where there's
            # no .exe to swap) - fall back to the download page.
            QDesktopServices.openUrl(QUrl(info.get("url", "")))
            return

        command = [updater] if updater.endswith(".exe") else [sys.executable, updater]
        command += [
            "--kind", "app",
            "--url", download,
            "--target", target,
            "--pid", str(os.getpid()),
            "--version", info.get("version", ""),
        ]
        try:
            subprocess.Popen(command, cwd=self._app_folder())
        except Exception as exc:  # noqa: BLE001
            show_error(
                self, "Couldn't start the updater",
                f"{exc}\n\nYou can download it yourself instead."
            )
            QDesktopServices.openUrl(QUrl(info.get("url", "")))
            return
        QTimer.singleShot(200, self.close)

    def on_no_update(self, silent):
        if not silent:
            QMessageBox.information(
                self, "You're up to date",
                f"You already have the latest version (v{APP_VERSION}).",
            )

    def on_update_check_failed(self, error_message, silent):
        if not silent:
            show_error(
                self, "Update check failed",
                f"Couldn't check for updates:\n{error_message}",
            )

    def set_theme(self, theme_name):
        """Switches the whole app between the light and dark stylesheets,
        and remembers the choice for next launch."""
        self.theme = theme_name
        QApplication.instance().setStyleSheet(
            DARK_STYLE if theme_name == "dark" else MAC_STYLE
        )
        self.title_bar.update_theme_label(theme_name)
        self.settings.setValue("theme", theme_name)

        # The Web Images tab styles most of its widgets in code rather than
        # through the app-wide sheet, so it needs telling directly.
        set_web_theme(theme_name)
        if getattr(self, "web_images_tab", None) is not None:
            self.web_images_tab.apply_theme()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.resize_grips is not None:
            self.resize_grips.reposition()

    def is_maximised(self):
        return self._maximised or self.isMaximized()

    def toggle_maximised(self):
        """Maximise/restore for a frameless window.

        Asking Qt to maximise a frameless window is unreliable - on Windows it
        can spill over the task bar - so the window is sized to the screen's
        work area by hand instead. That also means it fills the screen it's
        currently on, not always the primary one.
        """
        if self.is_maximised():
            if self.isMaximized():
                self.showNormal()
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            self._maximised = False
        else:
            self._normal_geometry = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            self.setGeometry(screen.availableGeometry())
            self._maximised = True
        self.title_bar.update_maximise_label(self._maximised)
        if self.resize_grips is not None:
            self.resize_grips.reposition()

    def changeEvent(self, event):
        # Keeps the button in step if the window is maximised another way
        # (Win+Up, snapping, the task bar).
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMaximized():
                self._maximised = True
            elif self.windowState() == Qt.WindowState.WindowNoState and self._maximised \
                    and not self.isMaximized():
                pass  # our own manual maximise - leave the flag alone
            self.title_bar.update_maximise_label(self.is_maximised())
        super().changeEvent(event)

    def closeEvent(self, event):
        # Stop the Web Images tab's timers / download threads cleanly so
        # closing the window doesn't leave anything running in the background.
        if getattr(self, "web_images_tab", None) is not None:
            self.web_images_tab.shutdown()
        if getattr(self, "flipbook_tab", None) is not None:
            self.flipbook_tab.play_timer.stop()
        super().closeEvent(event)

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        outer = QFrame()
        outer.setObjectName("OuterFrame")
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.title_bar = TitleBar(self)
        outer_layout.addWidget(self.title_bar)

        self.tabs = QTabWidget()
        # The native QTabBar's own internal size calculations turned out to
        # be unreliable for cleanly fitting this font/weight (labels kept
        # clipping regardless of padding/min-height tweaks), so it's hidden
        # entirely in favor of a custom row of pill buttons below, which
        # size themselves far more predictably.
        self.tabs.tabBar().hide()

        images_tab = QWidget()
        images_layout = QVBoxLayout(images_tab)
        images_layout.setContentsMargins(0, 0, 0, 0)
        images_layout.setSpacing(0)
        self._build_toolbar(images_layout)
        self._build_resize_row(images_layout)
        self._build_central_widget(images_layout)
        self._build_bottom_bar(images_layout)
        self.video_tab = VideoToGifTab(self)
        self.gif_optimiser_tab = GifOptimiserTab(self)
        self.image_optimiser_tab = ImageOptimiserTab(self)
        self.webp_to_gif_tab = WebpToGifTab(self)
        self.video_to_image_tab = VideoToImageTab(self)
        self.web_images_tab = WebImagesTab(self)
        self.flipbook_tab = WebpFlipbookTab(self)
        self.background_tab = (
            BackgroundRemoverTab(self) if NUMPY_AVAILABLE
            else BackgroundRemoverUnavailableTab(self)
        )
        self.image_creation_tab = ImageCreationTab(self)

        # The original tool tabs get the shared spacing pass; the Images,
        # Web Images and Flipbook tabs are laid out by hand already.
        for tab in (self.video_tab, self.gif_optimiser_tab, self.image_optimiser_tab,
                    self.webp_to_gif_tab, self.video_to_image_tab):
            apply_comfortable_spacing(tab)

        self.home_tab = HomeTab(self)
        pages = (
            (self.home_tab, "Home"),
            (images_tab, "Images"),
            (self.video_tab, "Video to GIF"),
            (self.gif_optimiser_tab, "GIF Optimiser"),
            (self.image_optimiser_tab, "Image Optimiser"),
            (self.webp_to_gif_tab, "WebP to GIF"),
            (self.video_to_image_tab, "Video to Image"),
            (self.web_images_tab, "Web Images"),
            (self.flipbook_tab, "WebP Flipbook"),
            (self.background_tab, "Background Remover"),
            (self.image_creation_tab, "Image Creation"),
        )
        # Tools whose component wasn't installed become a greyed-out tab
        # explaining how to add them, rather than vanishing.
        available = installed_components()
        self.missing_components = set()
        if available is not None:
            swapped = []
            for page, title in pages:
                component = COMPONENT_TABS.get(title)
                if component and component not in available:
                    self.missing_components.add(title)
                    swapped.append((NotInstalledTab(component, self), title))
                else:
                    swapped.append((page, title))
            pages = tuple(swapped)
            # The home tiles are built before this point, so refresh them now
            # that we know which tools are actually available.
            self.home_tab.rebuild()

        for page, title in pages:
            # The Web Images tab looks after its own space with a splitter and
            # its own scrollers, and its web views shouldn't be nested inside
            # another scroll area - that can stop mouse input reaching the page.
            wrapped = page if page is self.web_images_tab else wrap_in_scroll(page)
            self.tabs.addTab(wrapped, title)

        tab_bar_widget = self._build_pill_tab_bar()
        outer_layout.addWidget(tab_bar_widget)
        outer_layout.addWidget(self.tabs, stretch=1)

        self.setCentralWidget(outer)

    def _build_pill_tab_bar(self):
        container = QWidget()
        container.setObjectName("PillTabBarContainer")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(14, 8, 14, 0)
        layout.setSpacing(4)

        titles = [
            "Home", "Images", "Video to GIF", "GIF Optimiser", "Image Optimiser",
            "WebP to GIF", "Video to Image", "Web Images", "WebP Flipbook",
            "Background Remover", "Image Creation",
        ]
        button_group = QButtonGroup(container)
        button_group.setExclusive(True)

        self.tab_buttons = []
        for index, title in enumerate(titles):
            button = QPushButton(title)
            button.setObjectName("PillTabButton")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.PointingHandCursor)
            if title in getattr(self, "missing_components", set()):
                button.setToolTip(f"{title} isn't installed — open it to find out how to add it")
                button.setProperty("notInstalled", True)
            button.clicked.connect(lambda checked=False, i=index: self.on_pill_tab_clicked(i))
            button_group.addButton(button)
            layout.addWidget(button)
            self.tab_buttons.append(button)

        layout.addStretch()

        self.tab_indicator = QWidget(container)
        self.tab_indicator.setObjectName("TabIndicator")
        self.tab_indicator.setFixedHeight(3)
        self.tab_indicator.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.tab_indicator_animation = QPropertyAnimation(self.tab_indicator, b"geometry")
        self.tab_indicator_animation.setDuration(250)
        self.tab_indicator_animation.setEasingCurve(QEasingCurve.InOutCubic)

        # Button geometry isn't reliable until the window is actually laid
        # out, so position the indicator once the event loop starts.
        start_on_home = str(self.settings.value("start_on_home", "true")).lower() == "true"
        start_index = 0 if start_on_home else 1
        QTimer.singleShot(0, lambda: self.on_pill_tab_clicked(start_index))
        QTimer.singleShot(0, lambda: self._animate_tab_indicator(
            self.tab_buttons[start_index], instant=True))

        # There are more tabs than comfortably fit at the default window
        # width now, so the pill row lives in a thin horizontal scroller.
        # It behaves exactly as before whenever the window IS wide enough -
        # the scrollbar only appears when it's actually needed.
        self.pill_scroll = QScrollArea()
        self.pill_scroll.setObjectName("PillTabScroll")
        self.pill_scroll.setWidget(container)
        self.pill_scroll.setWidgetResizable(True)
        self.pill_scroll.setFrameShape(QFrame.NoFrame)
        self.pill_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pill_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.pill_scroll.setFixedHeight(container.sizeHint().height() + 12)
        return self.pill_scroll

    def show_tab(self, page):
        """Brings the tab containing `page` to the front."""
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            held = getattr(widget, "widget", lambda: None)()
            if widget is page or getattr(held, "page", None) is page or held is page:
                self.tab_buttons[index].click()
                return

    def on_pill_tab_clicked(self, index):
        self.tabs.setCurrentIndex(index)
        self.tab_buttons[index].setChecked(True)
        self._animate_tab_indicator(self.tab_buttons[index])
        # Keep the active pill on screen when the row is scrolled.
        self.pill_scroll.ensureWidgetVisible(self.tab_buttons[index], 40, 0)

    def _animate_tab_indicator(self, button, instant=False):
        inset = 10
        button_geometry = button.geometry()
        target_geometry = QRect(
            button_geometry.x() + inset,
            button_geometry.y() + button_geometry.height() - self.tab_indicator.height(),
            max(button_geometry.width() - inset * 2, 4),
            self.tab_indicator.height(),
        )

        self.tab_indicator_animation.stop()
        if instant:
            self.tab_indicator.setGeometry(target_geometry)
            self.tab_indicator.show()
            return

        self.tab_indicator_animation.setStartValue(self.tab_indicator.geometry())
        self.tab_indicator_animation.setEndValue(target_geometry)
        self.tab_indicator_animation.start()

    def _build_resize_row(self, parent_layout):
        """Output size controls, on their own row under the toolbar."""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(16, 0, 16, 6)
        self.resize_options = ResizeOptionsWidget()
        self.resize_options.changed.connect(self._update_status)
        row.addWidget(self.resize_options)
        parent_layout.addWidget(container)

    def _build_toolbar(self, parent_layout):
        toolbar_container = QWidget()
        toolbar_layout = QHBoxLayout(toolbar_container)
        toolbar_layout.setContentsMargins(16, 12, 16, 4)

        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)

        open_button = QToolButton()
        open_button.setText("Open")
        open_button.setPopupMode(QToolButton.InstantPopup)

        open_menu = QMenu(open_button)
        choose_one_action = QAction("Choose a File", self)
        choose_one_action.triggered.connect(self.choose_single_file)
        open_menu.addAction(choose_one_action)

        choose_many_action = QAction("Choose Multiple Files", self)
        choose_many_action.triggered.connect(self.choose_multiple_files)
        open_menu.addAction(choose_many_action)

        open_button.setMenu(open_menu)
        toolbar.addWidget(open_button)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Convert from: "))

        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMATS.keys())
        toolbar.addWidget(self.format_combo)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Convert to: "))

        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(OUTPUT_FORMATS.keys())
        toolbar.addWidget(self.output_format_combo)

        toolbar.addSeparator()

        self.convert_button = QPushButton("Convert")
        self.convert_button.setObjectName("ConvertButton")
        self.convert_button.setCursor(Qt.PointingHandCursor)
        self.convert_button.clicked.connect(self.on_convert_clicked)
        toolbar.addWidget(self.convert_button)

        toolbar_layout.addWidget(toolbar)
        parent_layout.addWidget(toolbar_container)

    def _build_central_widget(self, parent_layout):
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(22, 6, 22, 10)
        layout.setSpacing(16)

        left_box = QGroupBox("Selected Files")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(14, 14, 14, 14)
        self.selected_list = QListWidget()
        left_layout.addWidget(self.selected_list)
        left_box.setMinimumWidth(260)
        left_box.setMaximumWidth(320)

        right_box = QGroupBox("Completed")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(14, 14, 14, 14)
        self.completed_list = QListWidget()
        self.completed_list.setSpacing(3)
        right_layout.addWidget(self.completed_list)

        layout.addWidget(left_box)
        layout.addWidget(right_box, stretch=1)

        parent_layout.addWidget(central, stretch=1)

    def _build_bottom_bar(self, parent_layout):
        bottom_container = QWidget()
        bottom_layout = QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(22, 6, 22, 14)
        bottom_layout.setSpacing(12)

        credit = QLabel(MADE_WITH_TEXT)
        credit.setObjectName("MutedLabel")
        credit.setToolTip(f"{APP_TITLE} v{APP_VERSION}")
        bottom_layout.addWidget(credit)

        self.discord_button = QPushButton("Visit our Discord")
        self.discord_button.setCursor(Qt.PointingHandCursor)
        self.discord_button.setToolTip("Opens in your normal web browser")
        self.discord_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(DISCORD_URL))
        )
        # Hidden until DISCORD_URL is filled in at the top of this file.
        self.discord_button.setVisible(bool(DISCORD_URL))
        bottom_layout.addWidget(self.discord_button)

        self.status_bar = QStatusBar()
        self.status_bar.showMessage("Ready")
        bottom_layout.addWidget(self.status_bar, stretch=1)

        self.clear_completed_button = QPushButton("Clear Completed")
        self.clear_completed_button.setCursor(Qt.PointingHandCursor)
        self.clear_completed_button.setToolTip(
            "Remove finished/failed files from Completed to free up room "
            f"(limit is {MAX_FILES} files in progress at once)"
        )
        self.clear_completed_button.clicked.connect(self.on_clear_completed_clicked)
        bottom_layout.addWidget(self.clear_completed_button)

        self.download_all_button = QToolButton()
        self.download_all_button.setObjectName("DownloadAllButton")
        self.download_all_button.setText("Download All  ▾")
        self.download_all_button.setPopupMode(QToolButton.InstantPopup)
        self.download_all_button.setCursor(Qt.PointingHandCursor)

        download_menu = QMenu(self.download_all_button)
        save_found_action = QAction("Save to location found", self)
        save_found_action.triggered.connect(self.on_download_all_to_source)
        download_menu.addAction(save_found_action)

        save_chosen_action = QAction("Save to desired location", self)
        save_chosen_action.triggered.connect(self.on_download_all_to_chosen)
        download_menu.addAction(save_chosen_action)

        self.download_all_button.setMenu(download_menu)
        bottom_layout.addWidget(self.download_all_button)

        size_grip = QSizeGrip(bottom_container)
        bottom_layout.addWidget(size_grip, 0, Qt.AlignBottom | Qt.AlignRight)

        parent_layout.addWidget(bottom_container)

    # -- File selection -------------------------------------------------

    def _current_filter_string(self):
        name = self.format_combo.currentText()
        patterns = FORMATS[name]
        return f"{name} ({' '.join(patterns)})"

    def choose_single_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a file", "", self._current_filter_string()
        )
        if path:
            self.add_files([path])

    def choose_multiple_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose files", "", self._current_filter_string()
        )
        if paths:
            self.add_files(paths)

    def add_files(self, paths):
        if Image is None:
            show_critical(
                self,
                "Missing dependency",
                "Pillow is not installed. Run: pip install Pillow",
            )
            return

        total_in_pipeline = len(self.queued_items) + len(self.completed_rows)
        room_left = MAX_FILES - total_in_pipeline
        if room_left <= 0:
            show_error(
                self, "Limit reached",
                f"You already have {MAX_FILES} files in progress. Remove "
                f"some before adding more.",
            )
            return

        if len(paths) > room_left:
            show_error(
                self,
                "Too many files",
                f"You selected {len(paths)} files, but only {room_left} "
                f"more can be added (limit is {MAX_FILES} total). Only the "
                f"first {room_left} will be queued.",
            )
            paths = paths[:room_left]

        added_paths = []
        for path in paths:
            if path in self.queued_items or path in self.completed_rows:
                continue  # already in the pipeline

            widget = SelectedRowWidget(
                os.path.basename(path),
                path,
                on_remove=lambda checked=False, p=path: self.remove_queued_file(p),
            )
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self.selected_list.addItem(item)
            self.selected_list.setItemWidget(item, widget)

            self.queued_items[path] = {"item": item, "widget": widget}
            added_paths.append(path)

        # Auto-detect the "Convert from" format from what was just picked,
        # but only if the person hasn't manually chosen one themselves.
        if not self.format_manually_set and added_paths:
            first_ext = os.path.splitext(added_paths[0])[1].lower()
            format_name = EXTENSION_TO_FORMAT_NAME.get(first_ext)
            if format_name:
                self.format_combo.setCurrentText(format_name)

        # Warn if Selected Files now contains more than one file format —
        # only one format can be converted at a time.
        current_extensions = {os.path.splitext(p)[1].lower() for p in self.queued_items}
        if len(current_extensions) > 1:
            show_error(
                self, "Multiple file formats detected",
                "Selected Files contains more than one file format: "
                f"{', '.join(sorted(current_extensions))}.\n\n"
                "Please remove files (using the ✕ next to each one) so "
                "only a single format remains before converting.",
            )

        self._update_status()

    def remove_queued_file(self, path):
        entry = self.queued_items.pop(path, None)
        if entry:
            row = self.selected_list.row(entry["item"])
            self.selected_list.takeItem(row)
        self.submitted_paths.discard(path)
        self._update_status()

    def on_convert_clicked(self):
        if not self.queued_items:
            QMessageBox.information(self, "No files", "No files have been added yet.")
            return

        current_extensions = {os.path.splitext(p)[1].lower() for p in self.queued_items}
        if len(current_extensions) > 1:
            show_error(
                self, "Multiple file formats selected",
                "Selected Files still contains more than one file format: "
                f"{', '.join(sorted(current_extensions))}.\n\n"
                "Only one format can be converted at a time — remove files "
                "with the ✕ button until a single format remains.",
            )
            return

        for path, entry in self.queued_items.items():
            if path in self.submitted_paths:
                continue
            self.submitted_paths.add(path)
            entry["widget"].set_text(f"{os.path.basename(path)} — queued")
            entry["widget"].set_removable(False)

            worker = ConversionWorker(path)
            worker.signals.started.connect(self.on_conversion_started)
            worker.signals.finished.connect(self.on_conversion_finished)
            worker.signals.failed.connect(self.on_conversion_failed)
            self.thread_pool.start(worker)

        self._update_status()

    # -- Conversion lifecycle ---------------------------------------------

    def on_conversion_started(self, source_path):
        # Move the item out of "Selected Files" and into "Completed" as an
        # in-progress row with a progress bar.
        entry = self.queued_items.pop(source_path, None)
        if entry:
            row = self.selected_list.row(entry["item"])
            self.selected_list.takeItem(row)

        filename = os.path.basename(source_path)
        row_widget = CompletedRowWidget(
            filename,
            source_path,
            on_save=lambda checked=False, p=source_path: self.save_single(p, self._default_output_path(p)),
            on_save_to=lambda checked=False, p=source_path: self.save_single_to(p),
        )

        list_item = QListWidgetItem()
        list_item.setSizeHint(row_widget.sizeHint())
        self.completed_list.addItem(list_item)
        self.completed_list.setItemWidget(list_item, row_widget)

        self.completed_rows[source_path] = {"item": list_item, "widget": row_widget}
        self._update_status()

    def on_conversion_finished(self, source_path, image):
        self.converted_images[source_path] = image
        row = self.completed_rows.get(source_path)
        if row:
            row["widget"].mark_converted()
        self._update_status()

    def on_conversion_failed(self, source_path, error_message):
        self.failed_paths.add(source_path)
        row = self.completed_rows.get(source_path)
        if row:
            row["widget"].mark_failed(error_message)
        self._update_status()

    # -- Saving (per-row and bulk) ------------------------------------------

    def _current_output_format(self):
        name = self.output_format_combo.currentText()
        return OUTPUT_FORMATS[name]

    def _default_output_path(self, source_path):
        fmt = self._current_output_format()
        base_name = os.path.splitext(os.path.basename(source_path))[0]
        return os.path.join(os.path.dirname(source_path), base_name + fmt["ext"])

    def _write_image(self, source_path, output_path):
        image = self.converted_images.get(source_path)
        if image is None:
            return False, "Not converted yet"
        fmt = self._current_output_format()
        try:
            # Resizing happens at save time rather than at convert time, so
            # changing the output size doesn't mean converting everything again.
            image = resize_to_spec(image, self.resize_options.spec())
            prepared_image, extra_kwargs = _prepare_image_for_output(image, fmt["pillow_format"])
            prepared_image.save(output_path, fmt["pillow_format"], **extra_kwargs)
            return True, output_path
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def _delete_original(self, source_path):
        """Deletes the source file if the 'Delete original files after
        converting' option is on. Returns (deleted, error_message)."""
        if not self.delete_originals:
            return False, None
        try:
            os.remove(source_path)
            return True, None
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def save_single(self, source_path, output_path):
        success, result = self._write_image(source_path, output_path)
        row = self.completed_rows.get(source_path)
        if success:
            if row:
                row["widget"].flash_saved()
            self.status_bar.showMessage(f"Saved {os.path.basename(result)}")

            deleted, delete_error = self._delete_original(source_path)
            if deleted:
                QMessageBox.information(
                    self, "Original File Deleted",
                    f"Deleted original file:\n{os.path.basename(source_path)}",
                )
            elif delete_error:
                show_error(
                    self, "Couldn't delete original",
                    f"Saved the file, but couldn't delete the original file:\n{delete_error}",
                )

            if self.show_filenames_after_conversion:
                self.show_filenames_window([os.path.basename(result)])
        else:
            show_error(self, "Save failed", result)

    def save_single_to(self, source_path):
        fmt = self._current_output_format()
        default_path = self._default_output_path(source_path)
        filter_str = f"{fmt['pillow_format']} Image (*{fmt['ext']})"
        chosen_path, _ = QFileDialog.getSaveFileName(
            self, "Save Image As", default_path, filter_str
        )
        if chosen_path:
            self.save_single(source_path, chosen_path)

    def on_download_all_to_source(self):
        self._download_all(output_dir=None)

    def on_download_all_to_chosen(self):
        if not self.converted_images:
            QMessageBox.information(self, "Nothing to save", "No files have finished converting yet.")
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Choose a location to save your file(s) to"
        )
        if directory:
            self._download_all(output_dir=directory)

    def _download_all(self, output_dir):
        if not self.converted_images:
            QMessageBox.information(self, "Nothing to save", "No files have finished converting yet.")
            return

        fmt = self._current_output_format()
        saved_count = 0
        failed_count = 0
        saved_filenames = []
        deleted_files = []
        delete_failures = []

        for source_path in list(self.converted_images.keys()):
            if output_dir:
                base_name = os.path.splitext(os.path.basename(source_path))[0]
                output_path = os.path.join(output_dir, base_name + fmt["ext"])
            else:
                output_path = self._default_output_path(source_path)

            success, result = self._write_image(source_path, output_path)
            row = self.completed_rows.get(source_path)
            if success:
                saved_count += 1
                saved_filenames.append(os.path.basename(output_path))
                if row:
                    row["widget"].flash_saved()

                deleted, delete_error = self._delete_original(source_path)
                if deleted:
                    deleted_files.append(os.path.basename(source_path))
                elif delete_error:
                    delete_failures.append(os.path.basename(source_path))
            else:
                failed_count += 1

        message = f"Saved {saved_count} file(s)"
        if failed_count:
            message += f", {failed_count} failed"
        self.status_bar.showMessage(message)

        if self.show_filenames_after_conversion:
            self.show_filenames_window(saved_filenames)

        if deleted_files or delete_failures:
            notice_lines = []
            if deleted_files:
                notice_lines.append(
                    f"Deleted {len(deleted_files)} original file(s):\n"
                    + "\n".join(deleted_files)
                )
            if delete_failures:
                notice_lines.append(
                    f"Couldn't delete {len(delete_failures)} original file(s):\n"
                    + "\n".join(delete_failures)
                )
            QMessageBox.information(self, "Original Files Deleted", "\n\n".join(notice_lines))

    # -- Clear Completed ---------------------------------------------------

    def on_clear_completed_clicked(self):
        removable_paths = [
            path for path in self.completed_rows
            if path in self.converted_images or path in self.failed_paths
        ]

        if not removable_paths:
            QMessageBox.information(
                self, "Nothing to clear",
                "No finished or failed files to clear yet — files still "
                "converting are left alone.",
            )
            return

        for path in removable_paths:
            row = self.completed_rows.pop(path, None)
            if row:
                list_row = self.completed_list.row(row["item"])
                self.completed_list.takeItem(list_row)
            self.converted_images.pop(path, None)
            self.failed_paths.discard(path)
            self.submitted_paths.discard(path)

        self.status_bar.showMessage(f"Cleared {len(removable_paths)} file(s) from Completed")
        self._update_status()

    # -- Status bar ----------------------------------------------------------

    def _update_status(self):
        ready = sum(1 for p in self.queued_items if p not in self.submitted_paths)
        queued = sum(1 for p in self.queued_items if p in self.submitted_paths)
        converting = sum(
            1 for p in self.completed_rows if p not in self.converted_images
        )
        done = len(self.converted_images)

        if not ready and not queued and not converting and not done:
            self.status_bar.showMessage("Ready")
        else:
            parts = []
            if ready:
                parts.append(f"{ready} ready to convert")
            if queued:
                parts.append(f"{queued} queued")
            if converting:
                parts.append(f"{converting} converting")
            if done:
                parts.append(f"{done} ready to save")
            self.status_bar.showMessage(" · ".join(parts))


def configure_qt_attributes():
    """Application-wide Qt settings that must be applied before the
    QApplication is created.

    Qt 6 is high-DPI aware on its own (Qt 5 was not), so all that's needed
    here is the rounding policy: PassThrough keeps the app at the display's
    exact scale - 125%, 150% and so on - instead of rounding to whole
    numbers, which is what keeps the embedded browser's drawing and its
    click positions in step with each other.
    """
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, TypeError):
        pass

    # Both of these are no-ops on newer Qt but harmless, and still matter on
    # the older builds someone might have installed.
    for attribute_name in ("AA_DontCreateNativeWidgetSiblings",
                           "AA_ShareOpenGLContexts"):
        attribute = getattr(Qt.ApplicationAttribute, attribute_name, None)
        if attribute is not None:
            QApplication.setAttribute(attribute, True)


def main():
    configure_qt_attributes()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(app_icon())
    app.setStyleSheet(MAC_STYLE)
    install_error_hooks()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
