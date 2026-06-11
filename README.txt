==============================================================
  INTRO SONG SWAPPER  -  Forza Horizon 6
  Replace the "press start" intro music with any song you want
--------------------------------------------------------------
  Made by Esko Kustomz @ RevToolz
  https://eskokustomz.com/revtoolz
==============================================================


WHAT IT DOES
------------
Swaps the song in the game's startup / "press start" intro for
your own MP3, WAV, FLAC, etc. Includes a visual waveform editor
so you can pick the exact part of the song that plays, set the
volume, and preview it live before installing. Finds your game
automatically, backs up the original, and installs in one click.

  * Use any audio file (MP3, WAV, FLAC, OGG, M4A, AAC)
  * Waveform picker - drag a window to choose the part that plays
  * Live preview - hear it loop exactly like the game does
  * Live volume control
  * Automatic backup of the original intro
  * Auto-detects Steam AND Xbox / Game Pass installs
  * One-click restore to the original


HOW TO USE
----------
1. Run FH6IntroSongSwapper.exe
   (Click YES on the admin prompt - it needs that to write to
    the game folder. If Windows SmartScreen warns you, click
    "More info" then "Run anyway".)

2. Click BROWSE and choose your song. The waveform loads after
   a second or two.

3. Drag the PURPLE WINDOW on the waveform to pick the part you
   want. Drag the middle to slide it, drag the edges to resize.
   (Or type a Start time like 0:45 and a Length in seconds.)

4. Click PREVIEW to hear it loop like the menu. Drag the volume
   slider while it plays to set the level.

5. Click BUILD & INSTALL INTRO SONG. Done - launch the game!

To undo: click RESTORE ORIGINAL INTRO.


WHY ONLY ~80 SECONDS?
---------------------
The press-start screen has a fixed-length music slot (about 80
seconds) built into the game. When it ends, the game loops it.
This tool can't make the slot longer, so you pick the best ~80
second part of your song with the waveform. A smooth fade is
added so the loop sounds clean. (You only hear the loop if you
sit on the screen - press Start normally and you just hear the
start of the song.)


TROUBLESHOOTING
---------------
"Windows protected your PC" (SmartScreen):
  Normal for new tools that aren't code-signed. Click
  "More info" then "Run anyway". It's safe - it only touches
  the one intro audio file.

Antivirus flags it:
  False positive (common for Python/PyInstaller apps). Add an
  exception, or build from source yourself.

"Windows blocked writing to the game folder":
  Right-click the exe and choose "Run as administrator".

It didn't find my game:
  Point it at this file manually when asked:
  ...\Forza Horizon 6\Content\media\audio\fmodbanks\
     GLB_RadioPressStart.assets.bank

Song restarts in the middle / cuts off early / gap before loop:
  Adjust the Length value. Lower it if it cuts early, raise it
  if there's a silent gap. ~80 is the sweet spot.


BUILDING FROM SOURCE
--------------------
Don't trust a random .exe? Build it yourself - it's open source.

Requires Windows + Python 3.10+ (tick "Add Python to PATH"
during install: https://www.python.org/downloads/ ).

Easy way:   double-click build.bat
            (installs everything, makes dist\FH6IntroSongSwapper.exe)

Manual way:
  pip install -r requirements.txt
  python -c "import imageio_ffmpeg, shutil; shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), 'ffmpeg.exe')"
  pyinstaller --onefile --windowed --name FH6IntroSongSwapper --icon revtoolz.ico --add-binary "ffmpeg.exe;." --add-data "revtoolz.ico;." --add-data "revtoolz.png;." --collect-all sounddevice --exclude-module imageio_ffmpeg --exclude-module PIL --uac-admin --noconfirm FH6IntroSongSwapper.py

Or just run it without building:  python FH6IntroSongSwapper.py


DISCLAIMER
----------
Free, unofficial fan tool. Use at your own risk. It always backs
up your original intro before changing anything, and you can
restore it any time. Not affiliated with or endorsed by the
game's developers or publisher.


CREDITS
-------
Esko Kustomz @ RevToolz
https://eskokustomz.com/revtoolz

If you enjoy it, share it and tag RevToolz!
==============================================================
