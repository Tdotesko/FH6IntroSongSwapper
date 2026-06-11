@echo off
REM ============================================================
REM  Build script for Intro Song Swapper (RevToolz)
REM  Produces dist\FH6IntroSongSwapper.exe
REM ============================================================
setlocal

echo.
echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo [2/3] Staging bundled ffmpeg...
python -c "import imageio_ffmpeg, shutil; shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), 'ffmpeg.exe')"
if errorlevel 1 goto :error

echo.
echo [3/3] Building the .exe (this can take a minute)...
pyinstaller --onefile --windowed --name FH6IntroSongSwapper ^
  --icon revtoolz.ico ^
  --add-binary "ffmpeg.exe;." ^
  --add-data "revtoolz.ico;." ^
  --add-data "revtoolz.png;." ^
  --collect-all sounddevice ^
  --exclude-module imageio_ffmpeg ^
  --exclude-module PIL ^
  --uac-admin --noconfirm FH6IntroSongSwapper.py
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  DONE!  Your exe is in:  dist\FH6IntroSongSwapper.exe
echo ============================================================
del /q ffmpeg.exe >nul 2>&1
pause
exit /b 0

:error
echo.
echo BUILD FAILED. Make sure Python 3.10+ is installed and on PATH.
pause
exit /b 1
