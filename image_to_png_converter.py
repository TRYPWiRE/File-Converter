"""
File Converter - by Tryppy (Multi-format) - Mac-styled UI
=======================================================

A PyQt5 desktop app that converts image files to .png.

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

Requirements:
    pip install PyQt5 Pillow pillow-heif rawpy moviepy imageio-ffmpeg

Run:
    python image_to_png_converter.py
"""

import os
import sys
import shutil
import tempfile
import json
import urllib.request
import urllib.error
from datetime import datetime

from PyQt5.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal, QSize, QUrl, QTimer
from PyQt5.QtGui import QMovie, QIcon, QPixmap, QDesktopServices
from PyQt5.QtWidgets import (
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
    QAction,
    QComboBox,
    QLabel,
    QPushButton,
    QProgressBar,
    QSlider,
    QCheckBox,
    QTabWidget,
    QPlainTextEdit,
    QFileDialog,
    QMessageBox,
    QStatusBar,
    QSizeGrip,
)

try:
    from PIL import Image
except ImportError:
    Image = None

# Optional plugins - imported lazily/guarded so the app still runs without them
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    try:
        pillow_heif.register_avif_opener()
        AVIF_AVAILABLE = True
    except Exception:
        AVIF_AVAILABLE = False
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False
    AVIF_AVAILABLE = False

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


LOGO_PATH = resource_path("FClogo.png")

APP_TITLE = "File Converter - by Tryppy"
APP_VERSION = "1.0.0"

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


MAX_FILES = 10
MAX_CONCURRENT_CONVERSIONS = 2  # "1-2 at a time"

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
PREVIEW_BOX_MAX_WIDTH = 260
PREVIEW_BOX_MAX_HEIGHT = 200

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
    spacing: 6px;
}

QGroupBox {
    background-color: #ffffff;
    border: 1px solid #d9d9dc;
    border-radius: 10px;
    margin-top: 10px;
    font-weight: 600;
    padding-top: 6px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #4a4a4d;
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

QComboBox, QToolButton {
    background-color: #ffffff;
    border: 1px solid #d0d0d3;
    border-radius: 6px;
    padding: 4px 10px;
}

QComboBox:hover, QToolButton:hover {
    background-color: #f0f0f2;
}

QPushButton {
    border-radius: 8px;
    padding: 7px 18px;
    font-weight: 600;
    border: 1px solid #c9c9cc;
    background-color: #ffffff;
}

QPushButton:hover {
    background-color: #eeeeee;
}

QPushButton#DownloadAllButton {
    background-color: #007aff;
    color: #ffffff;
    border: none;
    padding: 8px 20px;
}

QPushButton#DownloadAllButton:hover {
    background-color: #2b8bff;
}

QPushButton#ConvertButton {
    background-color: #007aff;
    color: #ffffff;
    border: none;
    padding: 6px 18px;
}

QPushButton#ConvertButton:hover {
    background-color: #2b8bff;
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

QTabBar::tab {
    background-color: transparent;
    color: #6e6e73;
    padding: 8px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background-color: #ffffff;
    color: #1d1d1f;
    border: 1px solid #d9d9dc;
    border-bottom: none;
}

QTabBar::tab:hover:!selected {
    color: #1d1d1f;
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
    spacing: 6px;
}

QGroupBox {
    background-color: #2b2b2d;
    border: 1px solid #3a3a3c;
    border-radius: 10px;
    margin-top: 10px;
    font-weight: 600;
    padding-top: 6px;
    color: #f2f2f2;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #d1d1d6;
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

QLabel {
    color: #f2f2f2;
}

QComboBox, QToolButton {
    background-color: #3a3a3c;
    border: 1px solid #4a4a4c;
    border-radius: 6px;
    padding: 4px 10px;
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
    padding: 7px 18px;
    font-weight: 600;
    border: 1px solid #4a4a4c;
    background-color: #3a3a3c;
    color: #f2f2f2;
}

QPushButton:hover {
    background-color: #454547;
}

QPushButton#DownloadAllButton {
    background-color: #0a84ff;
    color: #ffffff;
    border: none;
    padding: 8px 20px;
}

QPushButton#DownloadAllButton:hover {
    background-color: #3b9dff;
}

QPushButton#ConvertButton {
    background-color: #0a84ff;
    color: #ffffff;
    border: none;
    padding: 6px 18px;
}

QPushButton#ConvertButton:hover {
    background-color: #3b9dff;
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

QTabBar::tab {
    background-color: transparent;
    color: #a1a1a6;
    padding: 8px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background-color: #2b2b2d;
    color: #f2f2f2;
    border: 1px solid #3a3a3c;
    border-bottom: none;
}

QTabBar::tab:hover:!selected {
    color: #f2f2f2;
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

        self.options_button = QToolButton()
        self.options_button.setObjectName("ThemeToggle")
        self.options_button.setText("Options")
        self.options_button.setPopupMode(QToolButton.InstantPopup)
        self.options_button.setCursor(Qt.PointingHandCursor)

        options_menu = QMenu(self.options_button)
        self.delete_originals_action = QAction("Delete original files after converting", self)
        self.delete_originals_action.setCheckable(True)
        self.delete_originals_action.toggled.connect(
            lambda checked: self._window.set_delete_originals(checked)
        )
        options_menu.addAction(self.delete_originals_action)

        options_menu.addSeparator()
        check_updates_action = QAction("Check for Updates…", self)
        check_updates_action.triggered.connect(
            lambda checked=False: self._window.check_for_updates(silent=False)
        )
        options_menu.addAction(check_updates_action)

        self.options_button.setMenu(options_menu)
        layout.addWidget(self.options_button)

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
        self.close_button = self._make_circle_button("✕", "#FF5F57", "#ff8c85")

        layout.addWidget(self.minimize_button)
        layout.addWidget(self.close_button)

        self.minimize_button.clicked.connect(self._window.showMinimized)
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

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self._window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self._window.move(event.globalPos() - self._drag_pos)
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
        self.save_to_button.setToolTip("Choose a location to save your file(s) to")
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
                    raise RuntimeError(
                        "AVIF support needs 'pillow-heif' (with AVIF plugin) "
                        "- run: pip install pillow-heif"
                    )
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
        self.preview_movie_label.setFixedSize(200, 150)
        self.preview_movie_label.setAlignment(Qt.AlignCenter)
        self.preview_movie_label.setStyleSheet(
            "border: 1px solid #d0d0d3; border-radius: 8px;"
        )
        result_row.addWidget(self.preview_movie_label)

        result_col = QVBoxLayout()
        self.size_label = QLabel("")
        self.size_label.setStyleSheet("font-weight: 700;")
        result_col.addWidget(self.size_label)

        buttons_row = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("RowSaveButton")
        self.save_button.setToolTip("Saves to same location file was found")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save_clicked)
        buttons_row.addWidget(self.save_button)

        self.save_to_button = QPushButton("Save To")
        self.save_to_button.setObjectName("RowSaveToButton")
        self.save_to_button.setToolTip("Choose a location to save your file(s) to")
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
            QMessageBox.warning(self, "Couldn't open video", str(exc))
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
        self.preview_movie_label.setFixedSize(200, 150)
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
        QMessageBox.warning(self, "GIF generation failed", error_message)

    def on_keep_original_width_toggled(self, checked):
        self.width_slider.setEnabled(not checked)
        if checked:
            self.width_label.setText("Original")
        else:
            self.width_label.setText(f"{self.width_slider.value()} px")

    # -- Save --------------------------------------------------------------

    def on_save_clicked(self):
        self._save_to(output_dir=None)

    def on_save_to_clicked(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Choose a location to save your file(s) to"
        )
        if directory:
            self._save_to(output_dir=directory)

    def _save_to(self, output_dir):
        if not self.temp_gif_path or not self.video_path:
            return
        folder = output_dir or os.path.dirname(self.video_path)
        base_name = os.path.splitext(os.path.basename(self.video_path))[0]
        destination = os.path.join(folder, base_name + ".gif")
        try:
            shutil.copy2(self.temp_gif_path, destination)
        except Exception as exc:  # noqa: BLE001
            self._log(f"ERROR saving to {destination}: {exc}")
            QMessageBox.warning(self, "Save failed", str(exc))
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
                QMessageBox.warning(
                    self, "Couldn't delete original",
                    f"Saved the GIF, but couldn't delete the original file:\n{delete_error}",
                )


# ---------------------------------------------------------------------------
# Update checker - looks at GitHub Releases in a background thread
# ---------------------------------------------------------------------------

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
                self.signals.update_available.emit({
                    "version": latest_tag,
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
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.resize(880, 560)

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

        self.theme = "light"
        self.delete_originals = False
        self.format_manually_set = False

        self._build_ui()

        self.format_combo.activated.connect(self.on_format_manually_changed)

        # Check for updates a couple seconds after launch, quietly - only
        # pop up a dialog if there's actually something new.
        QTimer.singleShot(2000, lambda: self.check_for_updates(silent=True))

    def on_format_manually_changed(self, index):
        self.format_manually_set = True

    def set_delete_originals(self, enabled):
        self.delete_originals = enabled

    def check_for_updates(self, silent=False):
        worker = UpdateCheckWorker()
        worker.signals.update_available.connect(self.on_update_available)
        worker.signals.no_update.connect(lambda: self.on_no_update(silent))
        worker.signals.failed.connect(lambda msg: self.on_update_check_failed(msg, silent))
        self.thread_pool.start(worker)

    def on_update_available(self, info):
        # Always shown, even for a silent/background check - finding an
        # actual update is worth interrupting for either way.
        answer = QMessageBox.question(
            self, "Update Available",
            f"A new version ({info['version']}) is available.\n"
            f"You're currently on v{APP_VERSION}.\n\n"
            "Open the download page?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl(info["url"]))

    def on_no_update(self, silent):
        if not silent:
            QMessageBox.information(
                self, "You're up to date",
                f"You already have the latest version (v{APP_VERSION}).",
            )

    def on_update_check_failed(self, error_message, silent):
        if not silent:
            QMessageBox.warning(
                self, "Update check failed",
                f"Couldn't check for updates:\n{error_message}",
            )

    def set_theme(self, theme_name):
        """Switches the whole app between the light and dark stylesheets."""
        self.theme = theme_name
        QApplication.instance().setStyleSheet(
            DARK_STYLE if theme_name == "dark" else MAC_STYLE
        )
        self.title_bar.update_theme_label(theme_name)

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

        images_tab = QWidget()
        images_layout = QVBoxLayout(images_tab)
        images_layout.setContentsMargins(0, 0, 0, 0)
        images_layout.setSpacing(0)
        self._build_toolbar(images_layout)
        self._build_central_widget(images_layout)
        self._build_bottom_bar(images_layout)
        self.tabs.addTab(images_tab, "Images")

        self.video_tab = VideoToGifTab(self)
        self.tabs.addTab(self.video_tab, "Video to GIF")

        outer_layout.addWidget(self.tabs, stretch=1)

        self.setCentralWidget(outer)

    def _build_toolbar(self, parent_layout):
        toolbar_container = QWidget()
        toolbar_layout = QHBoxLayout(toolbar_container)
        toolbar_layout.setContentsMargins(10, 4, 10, 4)

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
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        left_box = QGroupBox("Selected Files")
        left_layout = QVBoxLayout(left_box)
        self.selected_list = QListWidget()
        left_layout.addWidget(self.selected_list)
        left_box.setMinimumWidth(220)
        left_box.setMaximumWidth(260)

        right_box = QGroupBox("Completed")
        right_layout = QVBoxLayout(right_box)
        self.completed_list = QListWidget()
        self.completed_list.setSpacing(2)
        right_layout.addWidget(self.completed_list)

        layout.addWidget(left_box)
        layout.addWidget(right_box, stretch=1)

        parent_layout.addWidget(central, stretch=1)

    def _build_bottom_bar(self, parent_layout):
        bottom_container = QWidget()
        bottom_layout = QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(14, 4, 14, 4)

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
            QMessageBox.critical(
                self,
                "Missing dependency",
                "Pillow is not installed. Run: pip install Pillow",
            )
            return

        total_in_pipeline = len(self.queued_items) + len(self.completed_rows)
        room_left = MAX_FILES - total_in_pipeline
        if room_left <= 0:
            QMessageBox.warning(
                self, "Limit reached",
                f"You already have {MAX_FILES} files in progress. Remove "
                f"some before adding more.",
            )
            return

        if len(paths) > room_left:
            QMessageBox.warning(
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
            QMessageBox.warning(
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
            QMessageBox.warning(
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
            on_save=lambda checked=False, p=source_path: self.save_single(p, output_dir=None),
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

    def _write_png(self, source_path, output_dir):
        image = self.converted_images.get(source_path)
        if image is None:
            return False, "Not converted yet"
        folder = output_dir or os.path.dirname(source_path)
        base_name = os.path.splitext(os.path.basename(source_path))[0]
        output_path = os.path.join(folder, base_name + ".png")
        try:
            image.save(output_path, "PNG")
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

    def save_single(self, source_path, output_dir):
        success, result = self._write_png(source_path, output_dir)
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
                QMessageBox.warning(
                    self, "Couldn't delete original",
                    f"Saved the PNG, but couldn't delete the original file:\n{delete_error}",
                )
        else:
            QMessageBox.warning(self, "Save failed", result)

    def save_single_to(self, source_path):
        directory = QFileDialog.getExistingDirectory(
            self, "Choose a location to save your file(s) to"
        )
        if directory:
            self.save_single(source_path, output_dir=directory)

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

        saved_count = 0
        failed_count = 0
        deleted_files = []
        delete_failures = []

        for source_path in list(self.converted_images.keys()):
            success, result = self._write_png(source_path, output_dir)
            row = self.completed_rows.get(source_path)
            if success:
                saved_count += 1
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


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    if os.path.exists(LOGO_PATH):
        app.setWindowIcon(QIcon(LOGO_PATH))
    app.setStyleSheet(MAC_STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
