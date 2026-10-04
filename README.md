# File Converter — by Tryppy

A lightweight desktop app for converting images and video into more convenient formats — with a clean, macOS-inspired interface (light and dark modes), background processing so your PC doesn't get bogged down, and a couple of nice quality-of-life touches most simple converters skip.

## Features

### Images tab
- Convert **any of the following** into `.png`:
  - Common formats: JPG/JPEG, PNG, BMP, GIF, TIFF, ICO, PSD, WEBP, HEIC/HEIF, AVIF
- **Convert to** PNG, JPG, WEBP, **AVIF**, BMP, GIF, TIFF or ICO — so AVIF → PNG, AVIF → WEBP and PNG/WEBP → AVIF all work
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

### Web Images tab
*(merged in from the old standalone Web Image Browser)*

Has its own secondary nav bar with two pages:

- **Browser** — a real embedded Chromium browser (JavaScript, back/forward/reload, type plain words to search). **Collect Images From Page** scans everything the page actually rendered — `<img>`, `srcset`/`<picture>`, CSS backgrounds, SVG images, video posters, OpenGraph/Twitter images and embedded `data:` images. **Scroll Page + Collect** scrolls first so lazy-loaded images appear. Each image gets a preview card and can be saved as PNG, GIF, WebP or AVIF (animation kept where the format allows). Downloads reuse the browser's cookies, user agent and referer, so hot-link-protected images still work.
- **Overlay Studio** — place one image over another. Drag, nudge with arrow keys, 9 position presets, scale, opacity. Animated inputs stay animated when exported as GIF/WebP/AVIF. Send images straight from the Browser via each card's **⋯** menu, drag files in, or import from disk.

### WebP Flipbook tab
*(merged in from the old standalone WebP Flipbook Converter)*

- Converts **animated WebP → GIF** (original frame timings kept) and **flipbook / sprite-sheet WebP → animated GIF**
- **Keeps a transparent background** when the source has one (GIF transparency is all-or-nothing, so soft edges go fully solid or fully clear); turn it off to fill the background in instead
- **Auto Detect Grid** finds the rows/columns of a sprite sheet; the detected grid is drawn over the preview so you can see at a glance whether it lines up — override it manually if not
- Row → Column or Column → Row frame order, FPS, optional resize, optional PNG frame export, GIF optimisation
- Step through or **Play** the source preview

### Output size (Images, GIF Optimiser, Image Optimiser)

- An **Output size** row with presets for the common shapes — square 1:1, portrait 4:5, story 9:16, landscape 16:9, link preview, banner, icon — plus a custom width × height
- **Fit** keeps the whole image and pads the gaps (transparent, or white where the format can't store it), **Fill** crops to cover the frame, **Stretch** ignores the ratio
- **Allow upscaling** can be turned off so small images are centred at their own size rather than blown up
- In the optimisers the output comes out at exactly that size, and the compression slider then controls quality rather than dimensions. The Discord presets carry their own size, so they take precedence

### Background Remover tab

- Open an image and **click the background** to clear it — tolerance slider controls how much of a similar colour goes, and you can clear just the patch you clicked or that colour everywhere
- **Remove background around the edges** clears everything connected to the four corners in one go
- **Erase** and **Restore** brushes for anything the automatic pass gets wrong, with multi-step Undo
- **Soften / Tighten / Trim** to smooth the cut edge, shave off a colour halo, or crop away empty space
- **Zoom and pan** while you work: scroll to zoom in on wherever the cursor is, zoom/pan buttons under the preview, **Fit** to go back, and right- or middle-drag to shove the image around. Zoom only changes what you're looking at, never the image
- Saves as PNG, WebP or AVIF with real transparency (JPG isn't offered — it can't store it), keeping the original file name with the new extension
- **AI cutout** — a subject-detection model looks at the picture and works out what to keep, so it copes with busy backgrounds and subjects that share colours with them. Three models to pick from (fast 5 MB, best-quality 176 MB, and one trained on people/characters); the chosen model downloads itself on first use and is cached, then runs offline

### Window
- Maximise / restore from the green title-bar button, or by double-clicking the title bar; drag a maximised window to restore it
- **Resize from any edge or corner**, as well as the bottom-right grip
- The Background Remover tab has a **draggable divider** between their controls and their preview, so either side can be given more room
- Opens sized to fit whatever screen it's on, down to small and high-DPI displays; the WebP to GIF tab stacks its preview underneath the settings when the window is narrow

### General
- **Light / Dark mode** toggle, top-right
- Custom title bar with minimize/close buttons on the right (Windows-style), even though the overall look is macOS-inspired
- **Automatic update checks** against this repo's GitHub Releases, plus a manual "Check for Updates" option

## Requirements

- Python 3.9+
- [PyQt6](https://pypi.org/project/PyQt6/)
- [Pillow](https://pypi.org/project/Pillow/)
- [pillow-heif](https://pypi.org/project/pillow-heif/) — for HEIC/HEIF/AVIF support
- [rawpy](https://pypi.org/project/rawpy/) — for camera RAW support
- [pillow-avif-plugin](https://pypi.org/project/pillow-avif-plugin/) — for AVIF (not needed on Pillow 11.3+, which has it built in)
- [moviepy](https://pypi.org/project/moviepy/) and [imageio-ffmpeg](https://pypi.org/project/imageio-ffmpeg/) — for the Video to GIF tab
- [PyQt6-WebEngine](https://pypi.org/project/PyQt6-WebEngine/) and [requests](https://pypi.org/project/requests/) — for the Web Images tab's browser
- [numpy](https://pypi.org/project/numpy/) — for the WebP Flipbook tab's grid auto-detect

Install everything with:

```bash
pip install -r requirements.txt
```

> Every optional package degrades gracefully. The app always launches with all tabs; anything missing just shows a clear "install X" message where it's needed (e.g. without PyQtWebEngine the Browser page explains how to install it, but the Overlay Studio still works; without numpy you type the flipbook grid in yourself).

## Running from source

```bash
python image_to_png_converter.py
```

Make sure `FClogo.png` is in the same folder as the script — it's used for the in-app logo and the window/taskbar icon.

## Building a Windows .exe

A `build.bat` script is included that handles this for you:

1. Put `build.bat`, `prepare_icon.py`, `zip_app.py`, `image_to_png_converter.py` and your logo (`iGlogo.png` and `iGlogo.ico`, or the older `FClogo` files) in the same folder
2. Double-click `build.bat`

It will:
- Install PyInstaller if it's missing
- Check that this Python environment actually has `moviepy`/`imageio-ffmpeg` (a common gotcha if you have multiple Python installs)
- Check for (and install if missing) the Web Images / Flipbook / AVIF dependencies
- Use `FClogo.ico` as the exe, window and taskbar icon exactly as supplied (one is only generated from the PNG if the `.ico` is missing), and bundle both logo files into the exe
- Produce the app as a folder, `dist\ImageGen\` (run `ImageGen.exe` inside it, and keep the `_internal` folder next to it), plus `dist\ImageGen.zip` of that folder for a GitHub release

The app is built as a folder rather than one single `.exe` on purpose: a single `.exe` has to unpack itself into a temp folder every time it starts, which made opening the app take 20-40 seconds. The folder version starts in a couple of seconds.

You can re-run `build.bat` any time after making changes — it cleans up old build artifacts automatically.

## Updates

The app checks this repo's [Releases](../../releases) page for newer versions. To ship an update:

1. Bump `APP_VERSION` near the top of `image_to_png_converter.py`
2. Publish a new GitHub Release with a matching tag (e.g. `v1.1.0`), with `ImageGen.zip`, `ImageGenSetup.exe` and `ImageGenUpdater.exe` attached (the release workflow does this for you when you push the tag)

Users running an older version are notified automatically, and the updater downloads `ImageGen.zip` and swaps the new version in.

## License

Add your preferred license here.

## Author

**Tryppy** — [github.com/TRYPWiRE](https://github.com/TRYPWiRE)
