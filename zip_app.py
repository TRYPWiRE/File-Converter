"""
Packs the app folder that PyInstaller's --onedir build makes into one .zip
for the GitHub release.

    python zip_app.py          dist/ImageGen/  ->  dist/ImageGen.zip

The app ships as a folder rather than a single .exe because a single .exe
has to unpack itself into a temp folder on every launch - hundreds of MB,
each file scanned by antivirus - which is what made startup take 20-40
seconds. The folder starts in a couple of seconds.

The files sit at the top of the zip (ImageGen.exe, _internal/), so
unpacking it into the install folder is all the installer and updater do.
"""

import os
import sys
import zipfile

APP_NAME = "ImageGen"


def main():
    source = os.path.join("dist", APP_NAME)
    archive = os.path.join("dist", f"{APP_NAME}.zip")
    if not os.path.isfile(os.path.join(source, f"{APP_NAME}.exe")):
        sys.exit(f"{source}\\{APP_NAME}.exe not found - build the app first.")
    if os.path.exists(archive):
        os.remove(archive)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for root, _dirs, files in os.walk(source):
            for name in files:
                full = os.path.join(root, name)
                bundle.write(full, os.path.relpath(full, source))
    size = os.path.getsize(archive) / (1024 * 1024)
    print(f"Packed {archive} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
