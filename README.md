# File Converter — by Tryppy

A lightweight desktop app for converting images and video into more convenient formats — with a clean, macOS-inspired interface (light and dark modes), background processing so your PC doesn't get bogged down, and a couple of nice quality-of-life touches most simple converters skip.

## Features

### Images tab
- Convert **any of the following** into `.png`:
  - Common formats: JPG/JPEG, PNG, BMP, GIF, TIFF, ICO, PSD, WEBP, HEIC/HEIF, AVIF
  - Camera RAW formats: CR2, CR3, CRW, NEF, ARW, DNG, RAF, RW2, ORF, PEF, DCR, MRW, MOS, 3FR, X3F, ERF, RAW
- **"Convert from" dropdown** auto-detects the right format based on the files you pick — no need to set it manually
- Add up to **10 files at once**; only one format at a time can be queued together (you'll get a warning — and a ✕ button on each file — if you mix formats by accident)
- Conversion runs **1–2 files at a time** in the background, so it stays light on CPU/disk usage instead of blasting through everything at once
- Live per-file progress in the **Completed** panel, turning green once done
- **Save** (writes next to the original file) or **Save To** (choose a folder) — per file, or all at once with **Download All**
- **Clear Completed** button to free up room once you're done with a batch
- Optional **"Delete original files after converting"** setting (with a confirmation notification)

### Video to GIF tab
- Convert a single `.mp4` into an animated `.gif`
- Sliders for **start time**, **length**, **frame rate**, and **width** (or check **"Keep original"** to skip resizing entirely)
- **Generate Preview** actually renders the GIF and plays it back inline, showing the **real output file size** before you commit to saving (GIF size can't be reliably estimated in advance — only measured after encoding)
- A built-in **log** shows exactly what's happening: file sizes, settings used, and any errors, timestamped

### General
- **Light / Dark mode** toggle, top-right
- Custom title bar with minimize/close buttons on the right (Windows-style), even though the overall look is macOS-inspired
- **Automatic update checks** against this repo's GitHub Releases, plus a manual "Check for Updates" option

## Requirements

- Python 3.9+
- [PyQt5](https://pypi.org/project/PyQt5/)
- [Pillow](https://pypi.org/project/Pillow/)
- [pillow-heif](https://pypi.org/project/pillow-heif/) — for HEIC/HEIF/AVIF support
- [rawpy](https://pypi.org/project/rawpy/) — for camera RAW support
- [moviepy](https://pypi.org/project/moviepy/) and [imageio-ffmpeg](https://pypi.org/project/imageio-ffmpeg/) — for the Video to GIF tab

Install everything with:

```bash
pip install PyQt5 Pillow pillow-heif rawpy moviepy imageio-ffmpeg
```

> The Images tab works fine even without `rawpy`/`pillow-heif` — you'll just get a clear error if you try to convert a file type that needs one of them. Likewise, the Video to GIF tab will tell you plainly if `moviepy`/`imageio-ffmpeg` aren't installed, rather than crashing.

## Running from source

```bash
python image_to_png_converter.py
```

Make sure `FClogo.png` is in the same folder as the script — it's used for the in-app logo and the window/taskbar icon.

## Building a Windows .exe

A `build.bat` script is included that handles this for you:

1. Put `build.bat`, `image_to_png_converter.py`, and `FClogo.png` in the same folder
2. Double-click `build.bat`

It will:
- Install PyInstaller if it's missing
- Check that this Python environment actually has `moviepy`/`imageio-ffmpeg` (a common gotcha if you have multiple Python installs)
- Bundle the logo into the exe and auto-generate a proper `.ico` from it for the file/taskbar icon
- Produce `dist\File Converter - by Tryppy.exe`

You can re-run `build.bat` any time after making changes — it cleans up old build artifacts automatically.

## Updates

The app checks this repo's [Releases](../../releases) page for newer versions. To ship an update:

1. Bump `APP_VERSION` near the top of `image_to_png_converter.py`
2. Publish a new GitHub Release with a matching tag (e.g. `v1.1.0`)

Users running an older version will be notified automatically and can jump straight to the release page to download the update.

## License

## Author

**Tryppy** — [github.com/TRYPWiRE](https://github.com/TRYPWiRE)
