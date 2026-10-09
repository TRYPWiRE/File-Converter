@echo off
REM ============================================================
REM  Build script for File Converter - by Tryppy
REM  Double-click this file to build the .exe
REM ============================================================

setlocal

set SCRIPT_NAME=image_to_png_converter.py
set APP_NAME=ImageGen
echo.
echo === File Converter - by Tryppy - Build Script ===
echo.

REM Make sure the source script is present next to this .bat file
if not exist "%~dp0%SCRIPT_NAME%" (
    echo ERROR: Could not find %SCRIPT_NAME% in this folder.
    echo Make sure build.bat sits next to %SCRIPT_NAME%.
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"

REM Make sure PyInstaller is installed
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found - installing it now...
    pip install pyinstaller
    if errorlevel 1 (
        echo ERROR: Failed to install PyInstaller. Check your Python/pip setup.
        pause
        exit /b 1
    )
)

echo.
echo Using this Python:
python -c "import sys; print(sys.executable)"

echo.
echo Checking this Python can see moviepy / imageio-ffmpeg...
python -c "import moviepy, imageio_ffmpeg; print('moviepy + imageio-ffmpeg OK')" 2>nul
if errorlevel 1 (
    echo WARNING: moviepy/imageio-ffmpeg missing from THIS Python - installing...
    pip install moviepy imageio-ffmpeg
)

echo.
echo Checking PyQt6 + the Web Images browser engine...
python -c "import PyQt6.QtWebEngineWidgets, requests; print('PyQt6 + QtWebEngine + requests OK')" 2>nul
if errorlevel 1 (
    echo WARNING: PyQt6/PyQt6-WebEngine/requests missing from THIS Python - installing...
    pip install PyQt6 PyQt6-WebEngine requests
)
echo.
echo NOTE: this app now uses PyQt6 ^(Qt 6^). If you still have the old PyQt5
echo packages installed they are no longer used, and uninstalling them
echo ^(pip uninstall PyQt5 PyQtWebEngine^) avoids any confusion.

echo.
echo Checking numpy (WebP Flipbook grid detect + Background Remover)...
python -c "import numpy; print('numpy OK')" 2>nul
if errorlevel 1 (
    echo WARNING: numpy missing from THIS Python - installing...
    pip install numpy
)

echo.
echo Checking onnxruntime (Background Remover's AI cutout)...
python -c "import onnxruntime; print('onnxruntime OK')" 2>nul
if errorlevel 1 (
    echo WARNING: onnxruntime missing from THIS Python - installing...
    pip install onnxruntime
)

echo.
echo Checking AVIF support...
python -c "import sys; from PIL import features; ok = features.check('avif'); sys.exit(0 if ok else 1)" 2>nul
if errorlevel 1 (
    python -c "import pillow_avif" 2>nul
    if errorlevel 1 (
        echo Pillow here has no built-in AVIF - installing pillow-avif-plugin...
        pip install pillow-avif-plugin
    ) else (
        echo AVIF OK ^(pillow-avif-plugin^)
    )
) else (
    echo AVIF OK ^(built into Pillow^)
)

REM ------------------------------------------------------------
REM  App icon. prepare_icon.py picks the best logo available and
REM  makes sure the .ico really contains the small frames Windows
REM  needs - an .ico without them is the usual reason an exe shows
REM  the default icon even though the app window looks right.
REM ------------------------------------------------------------
set APP_ICON=
if exist "prepare_icon.py" (
    for /f "usebackq delims=" %%I in (`python prepare_icon.py`) do set "APP_ICON=%%I"
) else (
    echo WARNING: prepare_icon.py is missing - the exe may get a default icon.
)

set ICON_ARG=
if not "%APP_ICON%"=="" (
    echo Icon for the exe: %APP_ICON%
    set ICON_ARG=--icon "%APP_ICON%"
) else (
    echo WARNING: No usable icon found - the exe will use the default one.
)

REM Bundle the logo files so the app can draw them in its own title bar.
set ADD_DATA_ARGS=
if exist "iGlogo.png" set ADD_DATA_ARGS=%ADD_DATA_ARGS% --add-data "iGlogo.png;."
if exist "iGlogo.ico" set ADD_DATA_ARGS=%ADD_DATA_ARGS% --add-data "iGlogo.ico;."
if exist "FClogo.png" set ADD_DATA_ARGS=%ADD_DATA_ARGS% --add-data "FClogo.png;."
if exist "FClogo.ico" set ADD_DATA_ARGS=%ADD_DATA_ARGS% --add-data "FClogo.ico;."

echo.
echo Cleaning up previous build folders...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

echo.
echo Building "%APP_NAME%" ...
echo It's built as a folder rather than one big .exe: a single .exe has to
echo unpack itself into a temp folder every time it starts, which is what
echo made the app take 20-40 seconds to open.
echo.

echo Icon argument: %ICON_ARG%
echo.

python -m PyInstaller --onedir --windowed --clean --noconfirm --name "%APP_NAME%" ^
    --collect-all rawpy ^
    --collect-all pillow_heif ^
    --collect-all moviepy ^
    --collect-all imageio_ffmpeg ^
    --collect-all imageio ^
    --collect-all proglog ^
    --collect-all decorator ^
    --collect-all PIL ^
    --collect-all pillow_avif ^
    --hidden-import moviepy.editor ^
    --hidden-import PyQt6.QtWebEngineWidgets ^
    --hidden-import PyQt6.QtWebEngineCore ^
    --hidden-import PyQt6.sip ^
    --collect-all onnxruntime ^
    %ADD_DATA_ARGS% ^
    %ICON_ARG% ^
    "%SCRIPT_NAME%"

if errorlevel 1 (
    echo.
    echo ERROR: Build failed. Scroll up to see what PyInstaller reported.
    pause
    exit /b 1
)

echo.
echo Packing the app folder into dist\%APP_NAME%.zip for the release...
python zip_app.py
if errorlevel 1 (
    echo WARNING: couldn't make the .zip - the app folder itself is fine.
)

echo.
echo ============================================================
echo  Build complete!
echo  Run it from here:   dist\%APP_NAME%\%APP_NAME%.exe
echo  Keep the whole dist\%APP_NAME% folder together - the .exe
echo  needs the _internal folder next to it.
echo  For a GitHub release, upload dist\%APP_NAME%.zip
echo ============================================================
echo.
echo Refreshing the Windows icon cache so the new icon shows without
echo having to rename the exe...
ie4uinit.exe -show >nul 2>&1
ie4uinit.exe -ClearIconCache >nul 2>&1
del /f /q "%LOCALAPPDATA%\IconCache.db" >nul 2>&1
del /f /q "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*.db" >nul 2>&1
echo Icon cache cleared.

echo.
echo Windows keeps icons in memory as well as on disk. Restarting
echo Explorer picks the new one up immediately - your taskbar will
echo flicker for a second, nothing else is affected.
choice /c YN /n /m "Restart Explorer now? [Y/N] "
if errorlevel 2 goto skip_explorer
taskkill /f /im explorer.exe >nul 2>&1
start explorer.exe
echo Explorer restarted - the new icon should be showing.
:skip_explorer
echo.
pause
