"""
ImageGen - updater
==================

A small separate program that installs updates. It has to be separate,
because Windows won't let a running .exe overwrite itself.

The app launches this, then quits. This waits for it to close, downloads the
new file, swaps it in, and starts the app again.

Called like:

    ImageGenUpdater.exe --kind app
                        --url https://github.com/.../ImageGen.exe
                        --target "C:\\Program Files\\ImageGen\\ImageGen.exe"
                        --pid 1234
                        [--version 1.9.0] [--no-relaunch]

    ImageGenUpdater.exe --kind component --name web_images
                        --url https://github.com/.../component_web_images.zip
                        --target "C:\\Program Files\\ImageGen"
                        --pid 1234 [--version 1.9.0]

Nothing is deleted until the download has finished and been checked, and the
old file is kept until the new one is in place, so a failed update leaves the
working version behind.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
    QWidget,
)

APP_NAME = "ImageGen"
AUTHOR = "Tryppy1"
ACCENT = "#7a5cff"
ACCENT_2 = "#4f7cff"
COMPONENTS_FILE = "installed_components.json"
WAIT_FOR_EXIT_SECONDS = 30


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def stylesheet():
    return f"""
    QWidget {{
        background-color: #16161a; color: #f2f2f4;
        font-family: 'Segoe UI', Arial, sans-serif; font-size: 14px;
    }}
    QLabel#Title {{ font-size: 20px; font-weight: 700; }}
    QLabel#Status {{ color: #a1a1a8; }}
    QLabel#Footer {{ color: #7c7c84; font-size: 11px; }}
    QProgressBar {{
        background-color: #1e1e24; border: none; border-radius: 5px;
        height: 10px; text-align: center;
    }}
    QProgressBar::chunk {{
        border-radius: 5px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {ACCENT}, stop:1 {ACCENT_2});
    }}
    QPushButton {{
        background-color: #26262e; border: 1px solid #36363f;
        border-radius: 9px; padding: 8px 18px; font-weight: 600;
    }}
    QPushButton:hover {{ background-color: #31313a; }}
    """


def wait_for_exit(pid, timeout=WAIT_FOR_EXIT_SECONDS):
    """Waits for the app to close so its file can be replaced."""
    if not pid:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_is_running(pid):
            # Windows can hold the file open for a moment after the process
            # disappears, so give it a breath.
            time.sleep(1.0)
            return True
        time.sleep(0.4)
    return False


def process_is_running(pid):
    if os.name == "nt":
        try:
            output = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            return str(pid) in output
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


class UpdateSignals(QObject):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(str)   # message for the user
    failed = pyqtSignal(str)


class UpdateWorker(QRunnable):
    def __init__(self, options):
        super().__init__()
        self.options = options
        self.signals = UpdateSignals()

    # -- steps ---------------------------------------------------------------

    def download(self, url, destination):
        self.signals.status.emit("Downloading the update…")
        request = urllib.request.Request(
            url, headers={"User-Agent": f"{APP_NAME}-updater"})
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(destination, "wb") as handle:
                while True:
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if total:
                        self.signals.progress.emit(int(done / total * 95))
        if os.path.getsize(destination) == 0:
            raise RuntimeError("The download came through empty.")
        return destination

    def replace_app(self, downloaded, target):
        """Swaps the new exe in, keeping the old one until it's done."""
        self.signals.status.emit("Installing…")
        backup = target + ".old"
        if os.path.exists(backup):
            os.remove(backup)
        if os.path.exists(target):
            os.replace(target, backup)
        try:
            shutil.move(downloaded, target)
        except Exception:
            # Put the working version back rather than leaving nothing.
            if os.path.exists(backup):
                os.replace(backup, target)
            raise
        if os.path.exists(backup):
            try:
                os.remove(backup)
            except OSError:
                pass  # tidied up next time; the update itself worked

    def replace_component(self, downloaded, folder, name, version):
        self.signals.status.emit(f"Updating {name}…")
        if zipfile.is_zipfile(downloaded):
            with zipfile.ZipFile(downloaded) as archive:
                archive.extractall(folder)
            os.remove(downloaded)
        else:
            shutil.move(downloaded, os.path.join(folder, os.path.basename(downloaded)))
        self.record_component_version(folder, name, version)

    @staticmethod
    def record_component_version(folder, name, version):
        """Notes the new version so the app stops offering the same update."""
        path = os.path.join(folder, COMPONENTS_FILE)
        manifest = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    manifest = json.load(handle)
            except Exception:  # noqa: BLE001
                manifest = {}
        manifest.setdefault("installed", [])
        if name not in manifest["installed"]:
            manifest["installed"].append(name)
        manifest.setdefault("versions", {})[name] = version or "unknown"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)

    def run(self):
        options = self.options
        try:
            self.signals.status.emit("Waiting for the app to close…")
            if not wait_for_exit(options.pid):
                self.signals.failed.emit(
                    "The app is still running, so its files can't be replaced. "
                    "Close it and run the update again."
                )
                return

            temporary = os.path.join(
                os.path.dirname(os.path.abspath(options.target)) or ".",
                f".{APP_NAME}_update_download")
            downloaded = self.download(options.url, temporary)

            if options.kind == "app":
                self.replace_app(downloaded, options.target)
                message = f"{APP_NAME} has been updated."
            else:
                self.replace_component(downloaded, options.target,
                                       options.name, options.version)
                message = f"{options.name} has been updated."

            self.signals.progress.emit(100)
            self.signals.finished.emit(message)
        except urllib.error.URLError as exc:
            self.signals.failed.emit(f"Couldn't download the update: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


class UpdaterWindow(QWidget):
    def __init__(self, options):
        super().__init__()
        self.options = options
        self.done = False
        self.setWindowTitle(f"{APP_NAME} Updater")
        icon = resource("iGlogo.ico")
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.setFixedSize(460, 220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        logo_path = resource("iGlogo.png")
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                logo = QLabel()
                logo.setPixmap(pixmap.scaledToHeight(
                    34, Qt.TransformationMode.SmoothTransformation))
                header.addWidget(logo)
        title = QLabel(f"Updating {APP_NAME}")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        if options.version:
            version_label = QLabel(f"Version {options.version}")
            version_label.setObjectName("Status")
            layout.addWidget(version_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status = QLabel("Starting…")
        self.status.setObjectName("Status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()

        footer = QHBoxLayout()
        credit = QLabel(f"Made with \u2764 by {AUTHOR}")
        credit.setObjectName("Footer")
        footer.addWidget(credit)
        footer.addStretch()
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        self.close_button.setVisible(False)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

        self.pool = QThreadPool()
        QTimer.singleShot(300, self.start)

    def start(self):
        worker = UpdateWorker(self.options)
        worker.signals.progress.connect(self.progress.setValue)
        worker.signals.status.connect(self.status.setText)
        worker.signals.finished.connect(self.on_finished)
        worker.signals.failed.connect(self.on_failed)
        self.pool.start(worker)

    def on_finished(self, message):
        self.done = True
        self.status.setText(message)
        if self.options.kind == "app" and self.options.relaunch:
            self.status.setText(message + " Restarting…")
            QTimer.singleShot(800, self.relaunch)
        else:
            self.status.setText(
                message + " Start the app again to see the changes."
                if self.options.kind != "app" else message
            )
            self.close_button.setVisible(True)

    def on_failed(self, message):
        self.done = True
        self.status.setText(message)
        self.progress.setVisible(False)
        self.close_button.setVisible(True)

    def relaunch(self):
        target = self.options.target
        if self.options.kind == "component":
            target = os.path.join(self.options.target, f"{APP_NAME}.exe")
        try:
            if os.path.exists(target):
                subprocess.Popen([target], cwd=os.path.dirname(target) or None)
        except Exception:  # noqa: BLE001
            pass
        self.close()


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=f"{APP_NAME} updater")
    parser.add_argument("--kind", choices=("app", "component"), default="app")
    parser.add_argument("--url", required=True, help="what to download")
    parser.add_argument("--target", required=True,
                        help="the .exe to replace, or the install folder for a component")
    parser.add_argument("--pid", type=int, default=0, help="wait for this process to close")
    parser.add_argument("--name", default="", help="component id, for component updates")
    parser.add_argument("--version", default="", help="shown on screen and recorded")
    parser.add_argument("--no-relaunch", dest="relaunch", action="store_false",
                        help="don't start the app again afterwards")
    parser.set_defaults(relaunch=True)
    return parser.parse_args(argv)


def main():
    options = parse_arguments()
    app = QApplication(sys.argv)
    app.setStyleSheet(stylesheet())
    window = UpdaterWindow(options)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
