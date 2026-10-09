@echo off
REM ============================================================
REM  Builds the installer into a single small .exe
REM ============================================================
setlocal
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 pip install pyinstaller

set ICON_ARG=
if exist "prepare_icon.py" (
    for /f "usebackq delims=" %%I in (`python prepare_icon.py`) do set "APP_ICON=%%I"
)
if not "%APP_ICON%"=="" set ICON_ARG=--icon "%APP_ICON%"

set DATA_ARGS=
if exist "iGlogo.png" set DATA_ARGS=%DATA_ARGS% --add-data "iGlogo.png;."
if exist "iGlogo.ico" set DATA_ARGS=%DATA_ARGS% --add-data "iGlogo.ico;."

if exist build rmdir /s /q build
if exist "ImageGenSetup.spec" del /q "ImageGenSetup.spec"

python -m PyInstaller --onefile --windowed --clean --noconfirm ^
    --name "ImageGenSetup" ^
    %DATA_ARGS% ^
    %ICON_ARG% ^
    installer.py

echo.
echo Building the updater...
python -m PyInstaller --onefile --windowed --clean --noconfirm ^
    --name "ImageGenUpdater" ^
    %DATA_ARGS% ^
    %ICON_ARG% ^
    updater.py

echo.
echo ============================================================
echo  Built:
echo    dist\ImageGenSetup.exe    - the installer
echo    dist\ImageGenUpdater.exe  - the updater
echo.
echo  Upload BOTH to the GitHub release, along with ImageGen.zip
echo  from build.bat. The updater must sit next to ImageGen.exe
echo  once installed, or the app will fall back to opening the
echo  download page instead of updating itself.
echo ============================================================
pause
