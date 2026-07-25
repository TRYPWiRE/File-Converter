@echo off
REM ============================================================
REM  Build script for File Converter - by Tryppy
REM  Double-click this file to build the .exe
REM ============================================================

setlocal

set SCRIPT_NAME=image_to_png_converter.py
set APP_NAME=File Converter - by Tryppy
set LOGO_PNG=FClogo.png
set LOGO_ICO=FClogo.ico

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

set ADD_DATA_ARGS=
set ICON_ARG=
if exist "%LOGO_PNG%" (
    echo Found %LOGO_PNG% - it will be bundled into the exe and shown
    echo in the app's title bar and as the app/taskbar icon.
    set ADD_DATA_ARGS=--add-data "%LOGO_PNG%;."
) else (
    echo WARNING: %LOGO_PNG% not found next to build.bat.
    echo The app will still run, just without the logo shown.
)

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
echo Checking this Python can see moviepy / imageio-ffmpeg...
echo (Using: )
python -c "import sys; print(sys.executable)"
python -c "import moviepy, imageio_ffmpeg; print('moviepy + imageio-ffmpeg OK')" 2>nul
if errorlevel 1 (
    echo.
    echo WARNING: This Python can't import moviepy/imageio-ffmpeg, even if
    echo they're installed elsewhere - e.g. a different Python version, a
    echo virtual environment, or installed with 'pip' pointing somewhere else.
    echo The exe will build, but Video to GIF won't work in it.
    echo.
    echo Installing them now into THIS Python to be sure...
    pip install moviepy imageio-ffmpeg
    echo.
)

REM Build a proper multi-size .ico from the logo PNG for the exe's file
REM icon (Windows Explorer / taskbar) - PyInstaller's --icon needs .ico,
REM it can't use a .png directly.
if exist "%LOGO_PNG%" (
    echo.
    echo Generating %LOGO_ICO% from %LOGO_PNG% ...
    python -c "from PIL import Image; im = Image.open(r'%LOGO_PNG%').convert('RGBA'); im.save(r'%LOGO_ICO%', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    if errorlevel 1 (
        echo WARNING: Couldn't generate the .ico ^(is Pillow installed?^). Continuing without a custom exe icon.
    ) else (
        set ICON_ARG=--icon "%LOGO_ICO%"
    )
)

echo.
echo Cleaning up previous build folders...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

echo.
echo Building "%APP_NAME%.exe" ...
echo (This can take a minute or two the first time.)
echo.

python -m PyInstaller --onefile --windowed --name "%APP_NAME%" ^
    --collect-all rawpy ^
    --collect-all pillow_heif ^
    --collect-all moviepy ^
    --collect-all imageio_ffmpeg ^
    --collect-all imageio ^
    --collect-all proglog ^
    --collect-all decorator ^
    --hidden-import moviepy.editor ^
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
echo ============================================================
echo  Build complete!
echo  Your exe is here: dist\%APP_NAME%.exe
echo ============================================================
echo.
pause
