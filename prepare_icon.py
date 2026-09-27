"""Works out which icon the build should use, and makes sure it's one
Windows is happy with.

Windows Explorer picks the 16/32/48 px frames out of an .ico when it draws a
file in a folder. An .ico that only holds one big frame - which is what you
get from a lot of online PNG-to-ICO converters, and from saving a PNG under
an .ico name - often ends up showing the generic default icon on the exe even
though the same file works fine as the window icon inside the app.

So: find the best logo available, check the .ico really does contain the
small sizes, and rebuild it from the PNG when it doesn't.

Prints the path of the icon to use on stdout (and nothing else, so build.bat
can read it straight into a variable). Everything else goes to stderr.
"""

import os
import sys

# Newest branding first; the older names still work.
BASE_NAMES = ("iGlogo", "FClogo")
NEEDED_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                (128, 128), (256, 256)]
BUILD_ICON = "app_icon.ico"


def log(message):
    print(message, file=sys.stderr)


def find(extension):
    for name in BASE_NAMES:
        path = name + extension
        if os.path.exists(path):
            return path
    return None


def ico_is_usable(path):
    """True if this really is an .ico and it has the small frames Windows
    wants for the file listing."""
    try:
        from PIL import Image
    except ImportError:
        # Can't check, so trust it rather than blocking the build.
        return True
    try:
        with Image.open(path) as image:
            if (image.format or "").upper() != "ICO":
                log(f"  {path} isn't really an .ico file (it's {image.format}).")
                return False
            sizes = set(getattr(image, "ico", None).sizes()) if hasattr(image, "ico") \
                else set(image.info.get("sizes", []))
            if not sizes:
                sizes = {image.size}
            small = [size for size in sizes if size[0] <= 48]
            if not small:
                log(f"  {path} has no small frames ({sorted(sizes)}), so Explorer "
                    f"would fall back to a default icon.")
                return False
            return True
    except Exception as error:  # noqa: BLE001
        log(f"  Couldn't read {path}: {error}")
        return False


def build_ico_from_png(png_path):
    try:
        from PIL import Image
    except ImportError:
        log("  Pillow isn't installed, so a new .ico can't be generated.")
        return None
    try:
        with Image.open(png_path) as image:
            square = image.convert("RGBA")
            # Pad to a square first, or non-square logos come out stretched.
            if square.width != square.height:
                side = max(square.size)
                canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
                canvas.paste(square, ((side - square.width) // 2,
                                      (side - square.height) // 2))
                square = canvas
            sizes = [size for size in NEEDED_SIZES if size[0] <= max(square.size)] \
                or [(32, 32)]
            square.save(BUILD_ICON, format="ICO", sizes=sizes)
        log(f"  Built {BUILD_ICON} from {png_path} with sizes "
            f"{[size[0] for size in sizes]}.")
        return BUILD_ICON
    except Exception as error:  # noqa: BLE001
        log(f"  Couldn't build an .ico from {png_path}: {error}")
        return None


def main():
    png = find(".png")
    ico = find(".ico")
    log("Icon check:")
    log(f"  PNG: {png or 'none found'}")
    log(f"  ICO: {ico or 'none found'}")

    if ico and ico_is_usable(ico):
        log(f"  Using {ico} as-is.")
        print(ico)
        return

    if png:
        rebuilt = build_ico_from_png(png)
        if rebuilt:
            print(rebuilt)
            return

    if ico:
        # Better than nothing - PyInstaller may still manage it.
        log(f"  Falling back to {ico} even though it looks unusual.")
        print(ico)
        return

    log("  No logo found. Put iGlogo.png (and ideally iGlogo.ico) next to "
        "build.bat.")
    print("")


if __name__ == "__main__":
    main()
