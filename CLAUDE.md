# Notes for Claude

- Every change to the app must bump `APP_VERSION` in `image_to_png_converter.py`
  (patch number for fixes, e.g. 1.10.1 -> 1.10.2; minor for new features).
  The owner builds the .exe straight from the branch and the in-app update
  check compares this number against the GitHub release tag, so it has to
  move with every update.
- Errors shown to the user go through `show_error()` / `show_critical()`
  (never `QMessageBox.warning`/`critical`) so they get the "Copy error" button.
  Pass the traceback as `details=` where there is one.
