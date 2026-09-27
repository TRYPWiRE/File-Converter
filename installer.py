"""
ImageGen - installer
==========================

A small Windows installer for the app. It walks through a few pages, asks
which tools you want, downloads them from GitHub, and sets up shortcuts.

-----------------------------------------------------------------------------
EVERYTHING YOU'D WANT TO EDIT IS IN THE "CONTENT YOU CAN EDIT" BLOCK BELOW.
-----------------------------------------------------------------------------

The text uses HTML, so you can style it without touching any other code:

    <b>bold</b>                       <i>italic</i>
    <span class="big">larger</span>   <span class="small">smaller</span>
    <span class="hl">highlighted</span>
    <span class="accent">coloured</span>
    <ul><li>a bullet</li></ul>        <br> for a line break
    <a href="https://example.com">a link</a>

Run it with:      python installer.py
Build it with:    build_installer.bat
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

# =============================================================================
#  CONTENT YOU CAN EDIT
# =============================================================================

BRAND = {
    "app_name": "ImageGen",
    "author": "Tryppy1",
    # Shown at the bottom of every page, next to the logo.
    "made_with": "Made with <span class='heart'>&#10084;</span> by Tryppy1",
    # Put your logo next to this file. PNG is used on screen, ICO for the
    # window icon.
    "logo_png": "iGlogo.png",
    "logo_ico": "iGlogo.ico",
    # Paste your invite here and the Discord button starts working. Leave it
    # empty and the button hides itself.
    "discord_url": "",
    "website_url": "https://tko1.xyz",
}

# Where the app and its tools are downloaded from. Releases are read from
# GitHub, so publishing a new release is all it takes to ship an update.
GITHUB = {
    "owner": "TRYPWiRE",         # <- your GitHub username or organisation
    "repo": "File-Converter",    # <- the repository holding the releases
    # "latest" follows the newest release; pin a tag like "v1.7.0" instead
    # if you'd rather control exactly what installs.
    "release": "latest",
}

# The main program. Downloaded first, always.
CORE_ASSET = {
    "name": "Core application",
    "asset": "ImageGen.exe",
    "size_mb": 120,
    "description": "The app itself, plus the image and GIF converters.",
}

# Each tool the user can tick.
#
#   "asset"    the file name on the GitHub release, once the tool ships as
#              its own download
#   "id"       what the app looks for to decide whether the tool is on
#   "bundled"  True while the tool still lives inside the main .exe. The
#              installer then just switches it on instead of downloading
#              anything. Set it to False once you publish that component's
#              own file on the release, and the installer starts fetching
#              it - no other changes needed.
#
# Either way the tick boxes work the same, and anything unticked is greyed
# out in the app.
COMPONENTS = [
    {
        "id": "web_images",
        "bundled": True,
        "name": "Web Images",
        "asset": "component_web_images.zip",
        "size_mb": 180,
        "default": True,
        "description": (
            "Browse the web inside the app, collect every image a page shows, "
            "and composite them in the Overlay Studio. "
            "<span class='small'>Includes the embedded browser engine.</span>"
        ),
    },
    {
        "id": "video_tools",
        "bundled": True,
        "name": "Video tools",
        "asset": "component_video.zip",
        "size_mb": 90,
        "default": True,
        "description": (
            "Video to GIF and Video to Image, for pulling clips and frames "
            "out of recordings."
        ),
    },
    {
        "id": "flipbook",
        "bundled": True,
        "name": "WebP Flipbook",
        "asset": "component_flipbook.zip",
        "size_mb": 15,
        "default": True,
        "description": (
            "Turn animated and sprite-sheet WebP files into GIFs, with "
            "automatic grid detection and transparency."
        ),
    },
    {
        "id": "background_remover",
        "bundled": True,
        "name": "Background Remover",
        "asset": "component_bgremove.zip",
        "size_mb": 60,
        "default": True,
        "description": (
            "Cut people and characters out of their background with an AI "
            "model, then tidy up by hand. "
            "<span class='small'>The AI model itself downloads on first "
            "use, about 5 MB.</span>"
        ),
    },
    {
        "id": "image_creation",
        "bundled": True,
        "name": "Image Creation",
        "asset": "component_imagegen.zip",
        "size_mb": 2600,
        "default": False,
        "needs_gpu": True,
        "description": (
            "Generate pictures from a description, running entirely on this "
            "PC. <span class='hl'>This is the big one</span> - it brings in "
            "PyTorch and the model runtime."
        ),
    },
]

# The pages, in order. "body" is HTML - style it however you like.
PAGES = [
    {
        "key": "welcome",
        "title": f"Welcome to {BRAND['app_name']}",
        "subtitle": "A toolkit for converting, tidying and creating images.",
        "body": """
            <p class="big">Thanks for installing.</p>
            <p>This installer will put <b>ImageGen</b> on your PC and let
            you choose which tools come with it. Nothing is installed that you
            don't tick, and you can run the installer again later to add more.</p>
            <p class="small">It takes about a minute, depending on what you pick
            and your connection.</p>
        """,
    },
    {
        "key": "features",
        "title": "What it does",
        "subtitle": "Everything in one window, instead of five separate tools.",
        "body": """
            <ul>
              <li><b>Convert images</b> between PNG, JPG, WebP, AVIF, HEIC, RAW,
                  BMP, TIFF, ICO and more - in batches.</li>
              <li><b>Resize to shape</b> with presets for square, portrait,
                  story and landscape posts.</li>
              <li><b>Shrink GIFs and images</b> to a target size, with presets
                  for Discord emoji, stickers and upload limits.</li>
              <li><b>Video to GIF</b> and <b>Video to Image</b> for pulling
                  clips and frames out of recordings.</li>
              <li><b>Web Images</b> - a built-in browser that collects every
                  image a page displays.</li>
              <li><b>WebP Flipbook</b> - sprite sheets and animated WebP to GIF,
                  keeping transparency.</li>
              <li><b>Background Remover</b> - AI cutouts for people and
                  characters, with brushes for touching up.</li>
              <li><b>Image Creation</b> - generate images from a description,
                  locally on your own machine.</li>
            </ul>
        """,
    },
    {
        "key": "roadmap",
        "title": "What's coming next",
        "subtitle": "Where this is heading.",
        "body": """
            <p>Planned for future updates:</p>
            <ul>
              <li><span class="accent">Batch background removal</span> - run a
                  whole folder through in one go.</li>
              <li><span class="accent">Presets</span> - save your favourite
                  settings and reuse them in a click.</li>
              <li><span class="accent">More generation models</span> as good
                  ones become available to run locally.</li>
              <li><span class="accent">Watermarking and batch renaming.</span></li>
            </ul>
            <p class="small">The app checks GitHub for updates, so new tools and
            fixes arrive without reinstalling.</p>
        """,
    },
]

FINISH_BODY = """
    <p class="big">All done.</p>
    <p>ImageGen is installed and ready to use.</p>
    <p class="small">The app checks for updates on its own, so you'll be told
    when there's a new version or a new tool.</p>
"""

# =============================================================================
#  End of the editable content. Below here is the machinery.
# =============================================================================

ACCENT = "#7a5cff"
ACCENT_2 = "#4f7cff"
COMPONENTS_FILE = "installed_components.json"


def stylesheet():
    return f"""
    QWidget {{
        background-color: #16161a;
        color: #f2f2f4;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 14px;
    }}
    QLabel#Title {{ font-size: 26px; font-weight: 700; }}
    QLabel#Subtitle {{ color: #a1a1a8; font-size: 14px; }}
    QLabel#Body {{ font-size: 14px; }}
    QLabel#Footer {{ color: #8a8a92; font-size: 12px; }}
    QFrame#Card {{
        background-color: #1e1e24;
        border: 1px solid #2e2e36;
        border-radius: 12px;
    }}
    QFrame#Card:hover {{ border: 1px solid {ACCENT}; }}
    QFrame#Card QLabel {{ background: transparent; }}
    QFrame#Card QCheckBox {{ background: transparent; }}
    QFrame#Divider {{ background-color: #2e2e36; border: none; }}
    QPushButton {{
        background-color: #26262e;
        border: 1px solid #36363f;
        border-radius: 9px;
        padding: 9px 20px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background-color: #31313a; }}
    QPushButton:disabled {{ color: #6b6b73; background-color: #1d1d23; }}
    QPushButton#Primary {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {ACCENT}, stop:1 {ACCENT_2});
        border: none;
        color: #ffffff;
    }}
    QPushButton#Primary:hover {{ background: {ACCENT}; }}
    QPushButton#Link {{
        background: transparent; border: none; color: {ACCENT};
        padding: 4px 6px; text-align: left;
    }}
    QPushButton#Link:hover {{ color: #a98cff; text-decoration: underline; }}
    QCheckBox {{ spacing: 10px; font-weight: 600; }}
    QLineEdit {{
        background-color: #1e1e24; border: 1px solid #36363f;
        border-radius: 8px; padding: 8px 10px;
    }}
    QProgressBar {{
        background-color: #1e1e24; border: none; border-radius: 5px;
        height: 10px; text-align: center;
    }}
    QProgressBar::chunk {{
        border-radius: 5px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {ACCENT}, stop:1 {ACCENT_2});
    }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: #3a3a44; border-radius: 5px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """


def rich_text(html):
    """Wraps the editable HTML with the styles the tags above rely on."""
    return f"""
    <style>
      .big {{ font-size: 17px; }}
      .small {{ font-size: 12px; color: #9a9aa2; }}
      .hl {{ background-color: #3a2d6b; color: #ffffff; }}
      .accent {{ color: {ACCENT}; font-weight: 600; }}
      .heart {{ color: #ff5f7a; }}
      li {{ margin-bottom: 5px; }}
      a {{ color: {ACCENT}; }}
    </style>
    {html}
    """


def format_size(size_mb):
    if size_mb >= 1024:
        return f"{size_mb / 1024:.1f} GB"
    return f"{size_mb} MB"


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def default_install_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", BRAND["app_name"].replace(" ", ""))


# -- GPU detection ------------------------------------------------------------

def detect_gpu():
    """Returns (description, vram_gb). Uses nvidia-smi first, then Windows'
    own list, so it works without any Python packages installed."""
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        if output:
            name, memory = output.splitlines()[0].split(",")
            return name.strip(), round(int(memory.strip()) / 1024)
    except Exception:  # noqa: BLE001
        pass

    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController | "
             "Select-Object -First 1 -ExpandProperty Name)"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        if output:
            return output, 0
    except Exception:  # noqa: BLE001
        pass
    return "", 0


def recommend_model(gpu_name, vram_gb):
    """What to suggest for this machine, and what to warn about."""
    nvidia = "nvidia" in (gpu_name or "").lower() or "geforce" in (gpu_name or "").lower()
    if not gpu_name:
        return {
            "headline": "No graphics card detected",
            "recommended": "SD Turbo",
            "detail": (
                "Image generation will fall back to your processor. It does "
                "work, but expect <b>several minutes per image</b>. "
                "<span class='hl'>SD Turbo</span> is the one to use - it's the "
                "smallest and quickest."
            ),
            "warn": "Heavier models will be very slow on a CPU.",
        }
    if not nvidia:
        return {
            "headline": f"Found: {gpu_name}",
            "recommended": "SD Turbo",
            "detail": (
                "Local generation is fastest on NVIDIA cards, which this "
                "doesn't appear to be, so it will most likely run on the "
                "processor. <span class='hl'>SD Turbo</span> is the sensible "
                "starting point."
            ),
            "warn": "The larger models will be slow without an NVIDIA GPU.",
        }
    if vram_gb >= 12:
        return {
            "headline": f"Found: {gpu_name} ({vram_gb} GB)",
            "recommended": "FLUX.1 schnell",
            "detail": (
                "Plenty of memory. <span class='hl'>FLUX.1 schnell</span> is "
                "recommended - it's the closest thing to the big online "
                "generators that runs locally, and this card can hold it."
            ),
            "warn": "",
        }
    if vram_gb >= 8:
        return {
            "headline": f"Found: {gpu_name} ({vram_gb} GB)",
            "recommended": "SDXL 1.0",
            "detail": (
                "<span class='hl'>SDXL 1.0</span> is the sweet spot for this "
                "card. FLUX.1 will run too, but parts of it have to be shuffled "
                "between the card and main memory."
            ),
            "warn": "FLUX.1 is selectable, but expect it to be slow and to "
                    "stutter while it swaps data around.",
        }
    if vram_gb >= 4:
        return {
            "headline": f"Found: {gpu_name} ({vram_gb} GB)",
            "recommended": "SD Turbo",
            "detail": (
                "A smaller card. <span class='hl'>SD Turbo</span> or "
                "<span class='hl'>SDXL Turbo</span> will be comfortable and "
                "quick."
            ),
            "warn": "SDXL 1.0 and FLUX.1 may not fit and can fail with an "
                    "out-of-memory error.",
        }
    return {
        "headline": f"Found: {gpu_name}",
        "recommended": "SD Turbo",
        "detail": "Little video memory available, so stick to "
                  "<span class='hl'>SD Turbo</span>.",
        "warn": "The larger models are unlikely to fit.",
    }


# -- Downloading ---------------------------------------------------------------

def release_asset_url(asset):
    owner, repo = GITHUB["owner"], GITHUB["repo"]
    if GITHUB["release"] == "latest":
        return f"https://github.com/{owner}/{repo}/releases/latest/download/{asset}"
    return (f"https://github.com/{owner}/{repo}/releases/download/"
            f"{GITHUB['release']}/{asset}")


def asset_exists(asset):
    """Checks the release actually has this file, so a component that hasn't
    been published yet is skipped quietly instead of failing the install."""
    request = urllib.request.Request(release_asset_url(asset), method="HEAD",
                                     headers={"User-Agent": "ImageGenSetup"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return 200 <= response.status < 400
    except Exception:  # noqa: BLE001
        return False


class InstallSignals(QObject):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(list)   # anything that failed
    failed = pyqtSignal(str)


class InstallWorker(QRunnable):
    """Downloads the core app plus the chosen tools, then writes down what
    was installed so the app knows which tools to switch on."""

    def __init__(self, target_dir, chosen, shortcuts):
        super().__init__()
        self.target_dir = target_dir
        self.chosen = chosen
        self.shortcuts = shortcuts
        self.signals = InstallSignals()

    def _download(self, asset, destination, index, total):
        url = release_asset_url(asset)
        self.signals.status.emit(f"Downloading {asset}…")
        request = urllib.request.Request(url, headers={"User-Agent": "ImageGenSetup"})
        with urllib.request.urlopen(request, timeout=60) as response:
            size = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(destination, "wb") as handle:
                while True:
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if size:
                        share = (index + done / size) / total
                        self.signals.progress.emit(int(share * 100))

    def run(self):
        failures = []
        try:
            os.makedirs(self.target_dir, exist_ok=True)

            # The core app is required. The updater is strongly wanted but
            # the install is still usable without it. Components are only
            # fetched if they ship separately and are actually published.
            jobs = [(CORE_ASSET["asset"], True), ("ImageGenUpdater.exe", False)]
            for item in self.chosen:
                if not item.get("bundled"):
                    jobs.append((item["asset"], False))

            self.signals.status.emit("Checking what's available…")
            jobs = [(asset, required) for asset, required in jobs
                    if required or asset_exists(asset)]

            for index, (asset, required) in enumerate(jobs):
                destination = os.path.join(self.target_dir, asset)
                try:
                    self._download(asset, destination, index, len(jobs))
                    if asset.endswith(".zip"):
                        self.signals.status.emit(f"Unpacking {asset}…")
                        shutil.unpack_archive(destination, self.target_dir)
                        os.remove(destination)
                except Exception as exc:  # noqa: BLE001
                    message = f"{asset}: {exc}"
                    if not required:
                        message += " (optional - the app still works)"
                    failures.append(message)

            self.signals.status.emit("Writing settings…")
            write_component_manifest(self.target_dir, self.chosen)

            if self.shortcuts.get("desktop") or self.shortcuts.get("start_menu"):
                self.signals.status.emit("Creating shortcuts…")
                create_shortcuts(self.target_dir, self.shortcuts, failures)

            self.signals.progress.emit(100)
            self.signals.finished.emit(failures)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))


def write_component_manifest(target_dir, chosen):
    """The app reads this at startup to decide which tools are switched on."""
    manifest = {
        "app": BRAND["app_name"],
        "source": f"{GITHUB['owner']}/{GITHUB['repo']}",
        "installed": [item["id"] for item in chosen],
        "available": [item["id"] for item in COMPONENTS],
    }
    with open(os.path.join(target_dir, COMPONENTS_FILE), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest


def create_shortcuts(target_dir, shortcuts, failures):
    """Makes .lnk files through a short VBScript, which avoids needing any
    extra Python packages."""
    exe = os.path.join(target_dir, CORE_ASSET["asset"])
    icon = os.path.join(target_dir, BRAND["logo_ico"])
    places = []
    if shortcuts.get("desktop"):
        places.append(os.path.join(os.path.expanduser("~"), "Desktop"))
    if shortcuts.get("start_menu"):
        places.append(os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "Microsoft", "Windows", "Start Menu", "Programs",
        ))

    for folder in places:
        try:
            os.makedirs(folder, exist_ok=True)
            link = os.path.join(folder, BRAND["app_name"] + ".lnk")
            script = (
                'Set s = CreateObject("WScript.Shell")\n'
                f'Set l = s.CreateShortcut("{link}")\n'
                f'l.TargetPath = "{exe}"\n'
                f'l.WorkingDirectory = "{target_dir}"\n'
                f'l.Description = "{BRAND["app_name"]} by {BRAND["author"]}"\n'
                + (f'l.IconLocation = "{icon}"\n' if os.path.exists(icon) else "")
                + "l.Save\n"
            )
            script_path = os.path.join(tempfile.gettempdir(), "fc_shortcut.vbs")
            with open(script_path, "w", encoding="utf-8") as handle:
                handle.write(script)
            subprocess.run(["cscript", "//nologo", script_path], timeout=20,
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            os.remove(script_path)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Shortcut in {folder}: {exc}")


# -- Shared bits of the UI ------------------------------------------------------

def body_label(html):
    label = QLabel(rich_text(html))
    label.setObjectName("Body")
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setOpenExternalLinks(True)
    return label


def logo_pixmap(height):
    path = resource(BRAND["logo_png"])
    if not os.path.exists(path):
        return None
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    return pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)


class ComponentCard(QFrame):
    """One tickable tool."""

    def __init__(self, component):
        super().__init__()
        self.component = component
        self.setObjectName("Card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(component.get("default", True))
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(4)
        heading = QLabel(f"<b>{component['name']}</b>"
                         f"<span style='color:#8a8a92;'>  ·  "
                         f"{format_size(component['size_mb'])}</span>")
        heading.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(heading)
        column.addWidget(body_label(f"<span class='small'>{component['description']}</span>"))
        layout.addLayout(column, 1)

    def is_chosen(self):
        return self.checkbox.isChecked()

    def mousePressEvent(self, event):
        self.checkbox.toggle()


class Installer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{BRAND['app_name']} Setup")
        icon_path = resource(BRAND["logo_ico"])
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(880, 660)
        self.setMinimumSize(760, 600)
        self.pool = QThreadPool()
        self.component_cards = []
        self._build()

    # -- layout ---------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget()
        for page in PAGES:
            self.stack.addWidget(self._text_page(page))
        self.stack.addWidget(self._components_page())
        self.stack.addWidget(self._gpu_page())
        self.stack.addWidget(self._location_page())
        self.stack.addWidget(self._progress_page())
        self.stack.addWidget(self._finish_page())
        root.addWidget(self.stack, 1)

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        root.addWidget(divider)
        root.addWidget(self._footer())

    def _page_shell(self, title, subtitle):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 34, 40, 20)
        layout.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("Title")
        layout.addWidget(heading)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Subtitle")
            sub.setWordWrap(True)
            layout.addWidget(sub)
        return page, layout

    def _text_page(self, page_config):
        page, layout = self._page_shell(page_config["title"], page_config.get("subtitle"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 8, 0)
        holder_layout.addWidget(body_label(page_config["body"]))
        holder_layout.addStretch()
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        return page

    def _components_page(self):
        page, layout = self._page_shell(
            "Choose your tools",
            "Tick what you'd like. Anything you skip is greyed out in the app "
            "and can be added later by running this again."
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 8, 0)
        holder_layout.setSpacing(10)

        core = QFrame()
        core.setObjectName("Card")
        core_layout = QHBoxLayout(core)
        core_layout.setContentsMargins(16, 14, 16, 14)
        core_layout.addWidget(body_label(
            f"<b>{CORE_ASSET['name']}</b>"
            f"<span class='small'>  ·  {format_size(CORE_ASSET['size_mb'])}  ·  always "
            f"installed</span><br><span class='small'>{CORE_ASSET['description']}"
            f"</span>"
        ))
        holder_layout.addWidget(core)

        for component in COMPONENTS:
            card = ComponentCard(component)
            card.checkbox.toggled.connect(self._update_total)
            self.component_cards.append(card)
            holder_layout.addWidget(card)
        holder_layout.addStretch()
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)

        self.total_label = QLabel("")
        self.total_label.setObjectName("Subtitle")
        layout.addWidget(self.total_label)
        self._update_total()
        return page

    def _update_total(self):
        total = CORE_ASSET["size_mb"] + sum(
            card.component["size_mb"] for card in self.component_cards if card.is_chosen()
        )
        self.total_label.setText(f"Download size: about {format_size(total)}")

    def _gpu_page(self):
        page, layout = self._page_shell(
            "Your graphics card",
            "Image Creation runs on your own PC, so what it can do depends on "
            "this."
        )
        self.gpu_label = body_label("<p>Checking…</p>")
        layout.addWidget(self.gpu_label)
        layout.addStretch()
        return page

    def _refresh_gpu_page(self):
        name, vram = detect_gpu()
        advice = recommend_model(name, vram)
        warning = (f"<p><span class='hl'>Worth knowing:</span> {advice['warn']}</p>"
                   if advice["warn"] else "")
        self.gpu_label.setText(rich_text(f"""
            <p class="big">{advice['headline']}</p>
            <p>{advice['detail']}</p>
            {warning}
            <p class="small">Recommended to start with:
               <b>{advice['recommended']}</b>. Every model is selectable inside
               the app whatever this says - this is a suggestion, not a limit.
               Models download the first time you use them.</p>
        """))

    def _location_page(self):
        page, layout = self._page_shell("Where to install", "And what shortcuts to make.")
        row = QHBoxLayout()
        self.path_edit = QLineEdit(default_install_dir())
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.desktop_check = QCheckBox("Create a desktop shortcut")
        self.desktop_check.setChecked(True)
        layout.addWidget(self.desktop_check)
        self.start_menu_check = QCheckBox("Add it to the Start menu")
        self.start_menu_check.setChecked(True)
        layout.addWidget(self.start_menu_check)
        layout.addStretch()
        return page

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose where to install", self.path_edit.text())
        if folder:
            self.path_edit.setText(folder)

    def _progress_page(self):
        page, layout = self._page_shell("Installing", "This won't take long.")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.progress_status = QLabel("Starting…")
        self.progress_status.setObjectName("Subtitle")
        layout.addWidget(self.progress_status)
        layout.addStretch()
        return page

    def _finish_page(self):
        page, layout = self._page_shell("Ready to go", "")
        self.finish_label = body_label(FINISH_BODY)
        layout.addWidget(self.finish_label)
        layout.addStretch()
        return page

    def _footer(self):
        footer = QWidget()
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 12, 24, 14)
        layout.setSpacing(12)

        pixmap = logo_pixmap(26)
        if pixmap is not None:
            logo = QLabel()
            logo.setPixmap(pixmap)
            layout.addWidget(logo)

        credit = QLabel(rich_text(f"<span class='small'>{BRAND['made_with']}</span>"))
        credit.setObjectName("Footer")
        credit.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(credit)

        self.discord_button = QPushButton("Visit our Discord")
        self.discord_button.setObjectName("Link")
        self.discord_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discord_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(BRAND["discord_url"]))
        )
        # Hides itself until a link is filled in at the top of this file.
        self.discord_button.setVisible(bool(BRAND["discord_url"]))
        layout.addWidget(self.discord_button)
        layout.addStretch()

        self.back_button = QPushButton("Back")
        self.back_button.setMinimumWidth(110)
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.go_back)
        layout.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("Primary")
        # Wide enough for the longest label it takes ("Installing…").
        self.next_button.setMinimumWidth(130)
        self.next_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_button.clicked.connect(self.go_next)
        layout.addWidget(self.next_button)
        self._update_footer()
        return footer

    # -- navigation -------------------------------------------------------------

    @property
    def gpu_index(self):
        return len(PAGES) + 1

    @property
    def progress_index(self):
        return len(PAGES) + 3

    @property
    def finish_index(self):
        return len(PAGES) + 4

    def chosen_components(self):
        return [card.component for card in self.component_cards if card.is_chosen()]

    def _wants_generation(self):
        return any(item.get("needs_gpu") for item in self.chosen_components())

    def _update_footer(self):
        index = self.stack.currentIndex()
        self.back_button.setEnabled(0 < index < self.progress_index)
        if index == self.progress_index:
            self.next_button.setEnabled(False)
            self.next_button.setText("Installing…")
        elif index == self.finish_index:
            self.next_button.setEnabled(True)
            self.next_button.setText("Finish")
        elif index == self.progress_index - 1:
            self.next_button.setEnabled(True)
            self.next_button.setText("Install")
        else:
            self.next_button.setEnabled(True)
            self.next_button.setText("Next")

    def go_next(self):
        index = self.stack.currentIndex()
        if index == self.finish_index:
            self.close()
            return
        if index == self.progress_index - 1:
            self.start_install()
            return

        next_index = index + 1
        # The graphics card page is only relevant if Image Creation is wanted.
        if next_index == self.gpu_index and not self._wants_generation():
            next_index += 1
        elif next_index == self.gpu_index:
            self._refresh_gpu_page()
        self.stack.setCurrentIndex(next_index)
        self._update_footer()

    def go_back(self):
        index = self.stack.currentIndex() - 1
        if index == self.gpu_index and not self._wants_generation():
            index -= 1
        self.stack.setCurrentIndex(max(0, index))
        self._update_footer()

    # -- installing ---------------------------------------------------------------

    def start_install(self):
        target = self.path_edit.text().strip() or default_install_dir()
        self.stack.setCurrentIndex(self.progress_index)
        self._update_footer()
        worker = InstallWorker(
            target, self.chosen_components(),
            {"desktop": self.desktop_check.isChecked(),
             "start_menu": self.start_menu_check.isChecked()},
        )
        worker.signals.progress.connect(self.progress.setValue)
        worker.signals.status.connect(self.progress_status.setText)
        worker.signals.finished.connect(self._install_done)
        worker.signals.failed.connect(self._install_failed)
        self.pool.start(worker)

    def _install_done(self, failures):
        if failures:
            self.finish_label.setText(rich_text(
                "<p class='big'>Installed.</p>"
                "<p>These optional steps didn't complete:</p><ul>"
                + "".join(f"<li class='small'>{item}</li>" for item in failures)
                + "</ul><p class='small'>The app itself is installed and your "
                  "chosen tools are switched on. Running this installer again "
                  "will retry the rest.</p>"
            ))
        self.stack.setCurrentIndex(self.finish_index)
        self._update_footer()

    def _install_failed(self, message):
        QMessageBox.critical(self, "Installation failed", message)
        self.stack.setCurrentIndex(self.progress_index - 1)
        self._update_footer()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(stylesheet())
    window = Installer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
