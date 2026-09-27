"""
Builds the extension packages.

The main ImageGen.exe deliberately leaves the heavy libraries out (build.bat
passes --exclude-module for each of them), which is what keeps it small. This
script gathers those libraries into one .zip per extension, which the
installer and the in-app Extensions window download on demand and unpack into
extensions/<id>/lib/ next to the exe.

The app puts those folders on its import path at startup, so an installed
extension simply makes its tool work.

    python build_extensions.py                 build everything
    python build_extensions.py --only flipbook build one
    python build_extensions.py --list          show what would go in each

Output lands in dist/.
"""

import argparse
import fnmatch
import importlib.util
import os
import shutil
import site
import sys
import zipfile

OUTPUT_DIR = "dist"
STAGING_DIR = os.path.join("build", "extensions")

# Which libraries belong to which tool.
#
#   packages  imported names to copy wholesale, with their metadata
#   globs     extra files, matched against each site-packages folder - used
#             for Qt's WebEngine, which is a pile of DLLs rather than a
#             tidy importable package
#   optional  packages that may not be installed; skipped without complaint
EXTENSIONS = {
    "web_images": {
        "asset": "extension_web_images.zip",
        "packages": ["requests", "urllib3", "certifi", "charset_normalizer", "idna"],
        "globs": [
            "PyQt6/QtWebEngineWidgets*",
            "PyQt6/QtWebEngineCore*",
            "PyQt6/Qt6/bin/*WebEngine*",
            "PyQt6/Qt6/bin/QtWebEngineProcess*",
            "PyQt6/Qt6/resources/*",
            "PyQt6/Qt6/translations/qtwebengine_locales/*",
            "PyQt6/Qt6/libexec/QtWebEngineProcess*",
        ],
        "optional": ["charset_normalizer", "idna"],
    },
    "video_tools": {
        "asset": "extension_video.zip",
        "packages": ["moviepy", "imageio", "imageio_ffmpeg", "proglog", "decorator"],
        "globs": [],
        "optional": ["proglog", "decorator"],
    },
    "flipbook": {
        "asset": "extension_flipbook.zip",
        # The flipbook only needs numpy, which the core already carries, so
        # this one is tiny - it exists so the tool can still be switched on
        # and off like the others.
        "packages": [],
        "globs": [],
        "optional": [],
    },
    "background_remover": {
        "asset": "extension_bgremove.zip",
        "packages": ["onnxruntime", "coloredlogs", "humanfriendly", "flatbuffers",
                     "sympy", "mpmath"],
        "globs": [],
        "optional": ["coloredlogs", "humanfriendly", "flatbuffers", "sympy", "mpmath"],
    },
    "image_creation": {
        "asset": "extension_imagegen.zip",
        "packages": ["torch", "torchgen", "functorch", "diffusers", "transformers",
                     "safetensors", "accelerate", "huggingface_hub", "tokenizers",
                     "regex", "filelock", "fsspec", "networkx", "jinja2",
                     "markupsafe", "yaml", "psutil"],
        "globs": [],
        "optional": ["torchgen", "functorch", "networkx", "jinja2", "markupsafe",
                     "yaml", "psutil", "fsspec", "regex", "filelock"],
    },
}


def site_packages_dirs():
    folders = []
    for path in set(site.getsitepackages() + [site.getusersitepackages()]):
        if os.path.isdir(path):
            folders.append(path)
    return folders


def find_package(name):
    """Where a package lives on disk, or None."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, ModuleNotFoundError):
        return None
    if spec is None:
        return None
    if spec.submodule_search_locations:
        return list(spec.submodule_search_locations)[0]
    return spec.origin


def metadata_dirs(name, roots):
    """The .dist-info folders, which diffusers and friends read at import."""
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            lowered = entry.lower()
            if lowered.endswith((".dist-info", ".egg-info")) and \
                    lowered.split("-")[0] == name.lower().replace("_", "-").split("-")[0]:
                found.append(os.path.join(root, entry))
    return found


def copy_into(source, destination_root):
    name = os.path.basename(source)
    destination = os.path.join(destination_root, name)
    if os.path.isdir(source):
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    else:
        os.makedirs(destination_root, exist_ok=True)
        shutil.copy2(source, destination)
    return destination


def collect_globs(patterns, destination_root, roots):
    copied = 0
    for root in roots:
        for pattern in patterns:
            base = os.path.join(root, os.path.dirname(pattern))
            if not os.path.isdir(base):
                continue
            for entry in os.listdir(base):
                if not fnmatch.fnmatch(entry, os.path.basename(pattern)):
                    continue
                source = os.path.join(base, entry)
                target_dir = os.path.join(destination_root, os.path.dirname(pattern))
                os.makedirs(target_dir, exist_ok=True)
                if os.path.isdir(source):
                    shutil.copytree(source, os.path.join(target_dir, entry),
                                    dirs_exist_ok=True)
                else:
                    shutil.copy2(source, os.path.join(target_dir, entry))
                copied += 1
    return copied


def folder_size_mb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / (1024 * 1024)


def build_one(extension_id, config, roots, list_only=False):
    print(f"\n=== {extension_id} -> {config['asset']} ===")
    staging = os.path.join(STAGING_DIR, extension_id, "lib")
    if not list_only:
        shutil.rmtree(os.path.join(STAGING_DIR, extension_id), ignore_errors=True)
        os.makedirs(staging, exist_ok=True)

    missing = []
    for package in config["packages"]:
        location = find_package(package)
        if location is None:
            if package not in config.get("optional", []):
                missing.append(package)
                print(f"  MISSING (required): {package}")
            else:
                print(f"  skipped (not installed): {package}")
            continue
        print(f"  + {package}")
        if not list_only:
            copy_into(location, staging)
            for meta in metadata_dirs(package, roots):
                copy_into(meta, staging)

    if config["globs"]:
        if list_only:
            print(f"  + {len(config['globs'])} file patterns")
        else:
            copied = collect_globs(config["globs"], staging, roots)
            print(f"  + {copied} extra files from patterns")

    if missing:
        print(f"  !! not building - install these first: {', '.join(missing)}")
        return None
    if list_only:
        return None

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    archive = os.path.join(OUTPUT_DIR, config["asset"])
    size = folder_size_mb(staging)
    print(f"  packing {size:.0f} MB…")
    if os.path.exists(archive):
        os.remove(archive)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for root, _dirs, files in os.walk(os.path.dirname(staging)):
            for name in files:
                full = os.path.join(root, name)
                bundle.write(full, os.path.relpath(full, os.path.dirname(staging)))
    print(f"  done: {archive} ({os.path.getsize(archive) / (1024 * 1024):.0f} MB)")
    return archive


def main():
    parser = argparse.ArgumentParser(description="Build ImageGen extensions")
    parser.add_argument("--only", help="build just this extension")
    parser.add_argument("--list", action="store_true",
                        help="show what would go in, without building")
    options = parser.parse_args()

    roots = site_packages_dirs()
    print("Looking in:")
    for root in roots:
        print(f"  {root}")

    chosen = ({options.only: EXTENSIONS[options.only]} if options.only
              else EXTENSIONS)
    built = []
    for extension_id, config in chosen.items():
        archive = build_one(extension_id, config, roots, options.list)
        if archive:
            built.append(archive)

    if built:
        print("\nBuilt:")
        for archive in built:
            print(f"  {archive}")
        print("\nUpload these to the GitHub release alongside ImageGen.exe.")


if __name__ == "__main__":
    main()
