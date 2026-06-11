#!/usr/bin/env python3
"""
RevToolz - Intro Song Swapper
-----------------------------
Replaces the song inside GLB_RadioPressStart.assets.bank (the game's
"press start" intro) with any MP3 / WAV / FLAC, with volume control and
live preview. One button finds the game file, backs it up, and installs.

Self-contained: FMOD bank metadata template (event GUIDs, no audio),
ffmpeg, and artwork are embedded.
"""

import base64
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

try:
    import numpy as np
    import sounddevice as sd
    HAVE_LIVE = True
except Exception:
    HAVE_LIVE = False
try:
    import winsound          # fallback if sounddevice is unavailable
except ImportError:
    winsound = None

# ---------------------------------------------------------------------------
# Embedded bank metadata template: the 608 bytes that precede the audio.
# Contains the FMOD bank GUID + event references (NO copyrighted audio).
# ---------------------------------------------------------------------------
TEMPLATE_B64 = (
    "UklGRhiDfABGRVYgRk1UIAgAAACSAAAAkgAAAExJU1QqAgAAUFJPSkJOS0kgAAAAwM9lHTayIwUZ"
    "7QLocczZ+wAAAAAAAAAAAAAAAAUAAABMSVNUBAAAAElCU1NMSVNUBAAAAEdCU1NMSVNUBAAAAFJC"
    "U1NMSVNUBAAAAFBSVFNMSVNUBAAAAE1CU1NMSVNUBAAAAEJFRlhMSVNUBAAAAFBFRlhMSVNUBAAA"
    "AFNFRlhMSVNUBAAAAFNDRlhMSVNUBAAAAFNTRlhMSVNUBAAAAFZDQVNMSVNUBAAAAEVWVFNMSVNU"
    "BAAAAFRMTlNMSVNUBAAAAFBNTFNMSVNUBAAAAFBSTVNMSVNUBAAAAENUUlNMSVNUBAAAAENSVlNM"
    "SVNUBAAAAE1QR1NMSVNUBAAAAE1VSVNMSVNUBAAAAFNQSVNMSVNUBAAAAFBSSVNMSVNUBAAAAEVW"
    "SVNMSVNUBAAAAFdBSVNMSVNUBAAAAEVGSVNMSVNUBAAAAENNRFNMSVNUBAAAAFNMTlNMSVNUBAAA"
    "AExXVlNMSVNUNgAAAFdBVlNMQ05UBAAAAAEAAABXQVYgHgAAADG59+1z8EpGpVPj/Vb4E2YMAAAA"
    "AAAAAAAAAgAAAExJU1QEAAAAU05BU0xJU1QEAAAATU9EU1NOREgMAAAAAwAIAGACAADAgHwAU1RE"
    "VAAAAABTVEJMAAAAAEhBU0gYAAAAAwAUADG59+1z8EpGpVPj/Vb4E2a3zjmdREVMIAAAAABNVVRF"
    "AAAAAFJFRkkAAAAAUExBVAAAAABTTkQgyoB8AAAAAAAAAAAAAAA="
)

RATE, CH, WIDTH = 44100, 2, 2          # FMOD intro expects 44.1kHz stereo
LEAD_SILENCE_SEC = 40.0                # game seeks exactly 40s in before playing
BANK_NAME = "GLB_RadioPressStart.assets.bank"

# palette
BG, HEADER, CARD = "#0d0d12", "#15151e", "#1a1a24"
ACCENT, GREEN, RED = "#8b5cf6", "#22c55e", "#ef4444"
TEXT, MUTED, BORDER = "#ececf2", "#9a9aab", "#2a2a38"


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def ffmpeg_path():
    if getattr(sys, "frozen", False):
        cand = resource_path("ffmpeg.exe")
        if os.path.exists(cand):
            return cand
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------------------
# Audio / bank building
# ---------------------------------------------------------------------------
def _run_ffmpeg(args):
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    return subprocess.run([ffmpeg_path(), "-v", "error", *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=flags)


def _vol_filter(volume):
    return ["-af", f"volume={volume:.3f}"] if abs(volume - 1.0) > 1e-3 else []


def decode_to_pcm(audio_path, volume=1.0, start=0.0, length=None, fade=False):
    """Decode (optionally a start..start+length section, with fades) to raw PCM."""
    pre = ["-ss", f"{start:.3f}"] if start and start > 0 else []
    af = []
    if fade and length:
        af.append("afade=t=in:st=0:d=0.4")
        af.append(f"afade=t=out:st={max(0.0, length - 2.0):.3f}:d=2")
    if abs(volume - 1.0) > 1e-3:
        af.append(f"volume={volume:.3f}")
    args = [*pre, "-i", audio_path]
    if length:
        args += ["-t", f"{length:.3f}"]
    if af:
        args += ["-af", ",".join(af)]
    args += ["-ar", str(RATE), "-ac", str(CH),
             "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    proc = _run_ffmpeg(args)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("Could not read that audio file.\n\n"
                           + (proc.stderr.decode(errors="replace")[:400]
                              or "Unknown ffmpeg error"))
    return proc.stdout


def make_preview_wav(audio_path, volume, out_wav):
    proc = _run_ffmpeg(["-y", "-i", audio_path, *_vol_filter(volume),
                        "-ar", str(RATE), "-ac", str(CH), out_wav])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace")[:400] or "Preview failed")
    return out_wav


def build_fsb5(pcm):
    pad = (-len(pcm)) % 32
    data = pcm + b"\x00" * pad
    nframes = len(pcm) // (CH * WIDTH)
    raw = (8 << 1) | ((CH - 1) << 5) | (nframes << 34)   # 44100Hz, stereo
    sample_header = struct.pack("<Q", raw)
    header = struct.pack("<4s I I I I I I 8s 16s 8s",
                         b"FSB5", 1, 1, len(sample_header), 0, len(data), 2,
                         b"\x00" * 8, b"\x00" * 16, b"\x00" * 8)
    return header + sample_header + data


def lead_silence():
    return b"\x00" * (int(round(LEAD_SILENCE_SEC * RATE)) * CH * WIDTH)


def bank_from_section_pcm(section_pcm, log):
    """Build the .bank from a section of PCM (lead silence is prepended here)."""
    pcm = lead_silence() + section_pcm
    log(f"  added {LEAD_SILENCE_SEC:g}s lead-in so it starts on beat 1")
    fsb5 = build_fsb5(pcm)
    out = bytearray(base64.b64decode(TEMPLATE_B64)) + fsb5
    snd_off = out.find(b"SND ")
    snd_hdr_len = out.find(b"FSB5") - (snd_off + 8)
    struct.pack_into("<I", out, snd_off + 4, snd_hdr_len + len(fsb5))
    struct.pack_into("<I", out, 4, len(out) - 8)
    sndh = out.find(b"SNDH")
    flags, meta_off, _ = struct.unpack_from("<III", out, sndh + 8)
    struct.pack_into("<III", out, sndh + 8, flags, meta_off, len(fsb5))
    log(f"  built bank ({len(out):,} bytes)")
    return bytes(out)


def build_bank_bytes(audio_path, volume, log, start=0.0, length=None):
    log(f"Decoding section (volume {int(round(volume*100))}%)...")
    pcm = decode_to_pcm(audio_path, volume, start=start, length=length, fade=bool(length))
    log(f"  {len(pcm)/(RATE*CH*WIDTH):.1f}s decoded")
    return bank_from_section_pcm(pcm, log)


# ---------------------------------------------------------------------------
# Locate / install / restore
# ---------------------------------------------------------------------------
SKIP_DIRS = {"windows", "$recycle.bin", "perflogs", "temp", "tmp",
             "system volume information", "node_modules"}
PRIORITY_SUBDIRS = [
    "XboxGames\\Forza Horizon 6", "XboxGames",
    "Program Files (x86)\\Steam\\steamapps\\common",
    "Program Files\\Steam\\steamapps\\common",
    "SteamLibrary\\steamapps\\common", "Games",
    "Program Files\\WindowsApps",
]


def candidate_roots():
    roots = []
    for c in "CDEFGHIJKLMN":
        drive = f"{c}:\\"
        if os.path.exists(drive):
            for sub in PRIORITY_SUBDIRS:
                p = os.path.join(drive, sub)
                if os.path.exists(p):
                    roots.append(p)
    return roots


def _scan(roots, log):
    found, ticker = [], 0
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in SKIP_DIRS and "temp" not in d.lower()]
            ticker += 1
            if ticker % 400 == 0:
                log(f"  ...scanning {dirpath[:55]}")
            if BANK_NAME in filenames:
                p = os.path.join(dirpath, BANK_NAME)
                found.append(p)
                log(f"  FOUND: {p}")
    return found


def _dedupe(paths):
    seen, out = set(), []
    for p in paths:
        try:
            key = os.path.normcase(os.path.realpath(p))
        except Exception:
            key = os.path.normcase(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _prefer_writable(paths):
    real = [p for p in paths if "\\windowsapps\\" not in p.lower()]
    return real if real else paths


def find_banks(log):
    log("Looking in common game folders...")
    found = _dedupe(_scan(candidate_roots(), log))
    if not found:
        log("Not in the usual spots - scanning all drives (can take a minute)...")
        drives = [f"{c}:\\" for c in "CDEFGHIJKLMN" if os.path.exists(f"{c}:\\")]
        found = _dedupe(_scan(drives, log))
    found = _prefer_writable(found)
    if len(found) == 1:
        log(f"  using: {found[0]}")
    return found


def install_bank(new_bytes, target, log):
    bak = target + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(target, bak)
        log(f"  backup saved: {os.path.basename(bak)}")
    else:
        log("  backup already exists (kept original .bak)")
    with open(target, "wb") as f:
        f.write(new_bytes)
    log(f"  installed -> {target}")


# ---------------------------------------------------------------------------
# Live-gain audio preview (volume changes apply instantly while playing)
# ---------------------------------------------------------------------------
class LivePlayer:
    def __init__(self, on_finish=None):
        self.data = None          # (frames, 2) int16
        self.pos = 0
        self.gain = 1.0
        self.loop = False
        self.stream = None
        self.on_finish = on_finish

    def load(self, pcm_bytes):
        a = np.frombuffer(pcm_bytes, dtype=np.int16)
        self.data = a.reshape(-1, CH)

    def _cb(self, outdata, frames, time_info, status):
        if self.data is None:
            outdata.fill(0)
            raise sd.CallbackStop
        chunk = self.data[self.pos:self.pos + frames]
        n = len(chunk)
        if n:
            scaled = chunk.astype(np.float32) * self.gain
            np.clip(scaled, -32768, 32767, out=scaled)
            outdata[:n] = scaled.astype(np.int16)
        if n < frames:
            outdata[n:].fill(0)
            if self.loop:
                self.pos = 0          # restart (clip is faded, so wrap is silent)
            else:
                self.pos += n
                raise sd.CallbackStop
        else:
            self.pos += frames

    def play(self, gain, loop=False):
        self.stop()
        self.gain = gain
        self.loop = loop
        self.pos = 0
        self.stream = sd.OutputStream(
            samplerate=RATE, channels=CH, dtype="int16",
            callback=self._cb, finished_callback=self.on_finish)
        self.stream.start()

    def set_gain(self, gain):
        self.gain = gain

    def is_playing(self):
        return self.stream is not None and self.stream.active

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


# ---------------------------------------------------------------------------
# Waveform selector - draggable section window + live playhead (IG/TikTok style)
# ---------------------------------------------------------------------------
class WaveformSelector(tk.Canvas):
    def __init__(self, parent, width, height, on_change, **kw):
        super().__init__(parent, width=width, height=height, bg="#0f0f17",
                         highlightthickness=1, highlightbackground=BORDER, **kw)
        self.W, self.H = width, height
        self.on_change = on_change
        self.total = 0.0
        self.start = 0.0
        self.length = 80.0
        self.env = None
        self.bar_ids = []
        self.cols = 0
        self._sel_cols = (-1, -1)
        self._mode = None
        self._grab_dx = 0
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)

    # ---- geometry ----
    def _x(self, sec):
        return 0 if self.total <= 0 else sec / self.total * self.W
    def _sec(self, x):
        return 0 if self.total <= 0 else max(0.0, min(self.total, x / self.W * self.total))

    def show_placeholder(self, text):
        self.delete("all")
        self.env = None
        self.create_text(self.W / 2, self.H / 2, text=text, fill="#6a6a7c",
                         font=("Segoe UI", 10))

    # ---- data ----
    def set_song(self, song, total, length):
        self.total = total
        self.length = min(length, total)
        self.start = 0.0
        # build amplitude envelope
        self.cols = self.W // 2
        peaks = np.abs(song.astype(np.int32)).max(axis=1)
        edges = np.linspace(0, len(peaks), self.cols + 1).astype(np.int64)
        env = np.zeros(self.cols)
        for i in range(self.cols):
            a, b = edges[i], edges[i + 1]
            if b > a:
                env[i] = peaks[a:b].max()
        env = env / max(1.0, env.max())
        self.env = env
        self._draw()

    def _draw(self):
        self.delete("all")
        self.bar_ids = []
        mid = self.H / 2
        for i in range(self.cols):
            x = i * 2 + 1
            h = max(1.0, self.env[i] * (self.H * 0.42))
            self.bar_ids.append(self.create_line(x, mid - h, x, mid + h,
                                                 fill="#3a3a4c", width=2))
        # bracket frame + handles + playhead (created once, moved later)
        self.frame_top = self.create_line(0, 1, 0, 1, fill=ACCENT, width=2)
        self.frame_bot = self.create_line(0, self.H - 1, 0, self.H - 1, fill=ACCENT, width=2)
        self.h_left = self.create_rectangle(0, 0, 0, self.H, fill=ACCENT, outline=ACCENT)
        self.h_right = self.create_rectangle(0, 0, 0, self.H, fill=ACCENT, outline=ACCENT)
        self.playhead = self.create_line(0, 0, 0, self.H, fill="#ffffff", width=2,
                                         state="hidden")
        self._sel_cols = (-1, -1)
        self._refresh_selection()

    def _refresh_selection(self):
        if self.env is None:
            return
        xl, xr = self._x(self.start), self._x(self.start + self.length)
        # recolor only changed bars
        lo = max(0, int(xl // 2))
        hi = min(self.cols, int(xr // 2) + 1)
        plo, phi = self._sel_cols
        rng = range(min(lo, plo if plo >= 0 else lo), max(hi, phi if phi >= 0 else hi))
        for i in rng:
            if 0 <= i < self.cols:
                self.itemconfig(self.bar_ids[i],
                                fill=ACCENT if lo <= i < hi else "#3a3a4c")
        self._sel_cols = (lo, hi)
        self.coords(self.frame_top, xl, 1, xr, 1)
        self.coords(self.frame_bot, xl, self.H - 1, xr, self.H - 1)
        self.coords(self.h_left, xl - 3, 0, xl + 3, self.H)
        self.coords(self.h_right, xr - 3, 0, xr + 3, self.H)

    def set_length(self, length):
        if self.total <= 0:
            self.length = length
            return
        self.length = max(5.0, min(length, self.total))
        self.start = min(self.start, max(0.0, self.total - self.length))
        self._refresh_selection()

    def set_start(self, start):
        if self.total <= 0:
            self.start = start
            return
        self.start = max(0.0, min(start, self.total - self.length))
        self._refresh_selection()

    def set_playhead(self, sec):
        if self.total <= 0:
            return
        if sec is None:
            self.itemconfig(self.playhead, state="hidden")
        else:
            x = self._x(sec)
            self.coords(self.playhead, x, 0, x, self.H)
            self.itemconfig(self.playhead, state="normal")

    # ---- interaction ----
    def _press(self, e):
        if self.env is None:
            return
        xl, xr = self._x(self.start), self._x(self.start + self.length)
        if abs(e.x - xl) <= 7:
            self._mode = "left"
        elif abs(e.x - xr) <= 7:
            self._mode = "right"
        elif xl < e.x < xr:
            self._mode = "move"
            self._grab_dx = e.x - xl
        else:
            self._mode = "move"
            self._grab_dx = (xr - xl) / 2
            self._apply_move(e.x)

    def _drag(self, e):
        if self._mode == "move":
            self._apply_move(e.x)
        elif self._mode == "left":
            end = self.start + self.length
            ns = min(self._sec(e.x), end - 5.0)
            self.start = max(0.0, ns)
            self.length = end - self.start
            self._refresh_selection()
        elif self._mode == "right":
            self.length = max(5.0, min(self._sec(e.x) - self.start, self.total - self.start))
            self._refresh_selection()

    def _apply_move(self, x):
        ns = self._sec(x - self._grab_dx)
        self.start = max(0.0, min(ns, self.total - self.length))
        self._refresh_selection()

    def _release(self, e):
        if self._mode and self.on_change:
            self.on_change(self.start, self.length)
        self._mode = None


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RevToolz - Intro Song Swapper")
        self.geometry("680x790")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._ico = resource_path("revtoolz.ico")
        try:
            self.iconbitmap(default=self._ico)
        except Exception:
            pass

        self.audio_path = tk.StringVar()
        self.vol = tk.DoubleVar(value=100)
        self.start_str = tk.StringVar(value="0:00")   # where the intro section begins
        self.len_str = tk.StringVar(value="80")       # seconds that fit the slot
        self._preview_wav = None
        self._song = None           # full decoded song, numpy (frames, 2) int16
        self._song_src = None
        self._total = 0.0
        self._sync = False          # guard against field<->waveform feedback loops
        self._player = LivePlayer(on_finish=self._preview_ended) if HAVE_LIVE else None
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._style()
        self._build_header()
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(4, 18))
        self._build_song(body)
        self._build_section(body)
        self._build_volume(body)
        self._build_actions(body)
        self._build_log(body)

    # ---- styling helpers --------------------------------------------------
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Rev.Horizontal.TScale", background=CARD,
                     troughcolor="#2c2c3c", bordercolor=CARD,
                     lightcolor=ACCENT, darkcolor=ACCENT)

    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(14, 4))

    def _hoverable(self, btn, normal, hover):
        btn.bind("<Enter>", lambda e: btn.config(bg=hover))
        btn.bind("<Leave>", lambda e: btn.config(bg=normal))
        return btn

    # ---- sections ---------------------------------------------------------
    def _build_header(self):
        bar = tk.Frame(self, bg=HEADER, height=92)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        try:
            self._logo = tk.PhotoImage(file=resource_path("revtoolz.png"))
            tk.Label(bar, image=self._logo, bg=HEADER).pack(side="left", padx=(22, 14), pady=10)
        except Exception:
            pass
        tx = tk.Frame(bar, bg=HEADER)
        tx.pack(side="left", anchor="w", pady=18)
        tk.Label(tx, text="Intro Song Swapper", bg=HEADER, fg=TEXT,
                 font=("Segoe UI Semibold", 20)).pack(anchor="w")
        tk.Label(tx, text="Replace the game's press-start intro with your own song",
                 bg=HEADER, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        self._build_credit(bar)
        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")

    def _build_credit(self, bar):
        url = "https://eskokustomz.com/revtoolz"
        credit = tk.Frame(bar, bg=HEADER)
        credit.pack(side="right", padx=22)
        l1 = tk.Label(credit, text="made by Esko Kustomz", bg=HEADER, fg=MUTED,
                      font=("Segoe UI", 8), cursor="hand2")
        l1.pack(anchor="e")
        l2 = tk.Label(credit, text="@ RevToolz", bg=HEADER, fg=ACCENT,
                      font=("Segoe UI", 10, "bold underline"), cursor="hand2")
        l2.pack(anchor="e")
        for w in (credit, l1, l2):
            w.bind("<Button-1>", lambda e: webbrowser.open(url))
        # subtle hover feedback on the link
        l2.bind("<Enter>", lambda e: l2.config(fg="#a98bff"))
        l2.bind("<Leave>", lambda e: l2.config(fg=ACCENT))

    def _card(self, parent):
        c = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                     highlightthickness=1)
        c.pack(fill="x")
        return c

    def _build_song(self, body):
        self._section(body, "YOUR SONG")
        c = self._card(body)
        row = tk.Frame(c, bg=CARD)
        row.pack(fill="x", padx=12, pady=12)
        e = tk.Entry(row, textvariable=self.audio_path, font=("Segoe UI", 10),
                     bg="#0f0f15", fg=TEXT, insertbackground=TEXT,
                     relief="flat", disabledbackground="#0f0f15")
        e.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 10))
        b = tk.Button(row, text="Browse", command=self.pick_audio, bg=ACCENT,
                      fg="white", relief="flat", font=("Segoe UI", 9, "bold"),
                      padx=16, cursor="hand2", activebackground="#7a4fe0")
        b.pack(side="left")
        self._hoverable(b, ACCENT, "#9d72ff")

    def _build_section(self, body):
        self._section(body, "INTRO SECTION  (drag the purple window to pick the part)")
        c = self._card(body)
        inner = tk.Frame(c, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)
        self.wave = WaveformSelector(inner, width=608, height=92,
                                     on_change=self._on_wave_change)
        self.wave.pack(fill="x")
        self.wave.show_placeholder("Choose a song above to load its waveform")
        r = tk.Frame(inner, bg=CARD)
        r.pack(fill="x", pady=(10, 0))
        tk.Label(r, text="Start", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10)).pack(side="left")
        tk.Entry(r, textvariable=self.start_str, width=7, font=("Segoe UI", 10),
                 bg="#0f0f15", fg=TEXT, insertbackground=TEXT, relief="flat",
                 justify="center").pack(side="left", padx=(8, 12), ipady=4)
        tk.Label(r, text="Length", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10)).pack(side="left")
        tk.Entry(r, textvariable=self.len_str, width=5, font=("Segoe UI", 10),
                 bg="#0f0f15", fg=TEXT, insertbackground=TEXT, relief="flat",
                 justify="center").pack(side="left", padx=(8, 4), ipady=4)
        tk.Label(r, text="sec", bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        tk.Label(r, text="drag window = move  •  drag edges = resize",
                 bg=CARD, fg="#7a7a8a", font=("Segoe UI", 8)).pack(side="right")
        # two-way sync: typing in the fields moves the waveform window
        self.start_str.trace_add("write", self._on_fields)
        self.len_str.trace_add("write", self._on_fields)

    def _build_volume(self, body):
        self._section(body, "VOLUME")
        c = self._card(body)
        inner = tk.Frame(c, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)
        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text="Adjust before installing", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.vol_lbl = tk.Label(top, text="100%", bg=CARD, fg=ACCENT,
                                font=("Segoe UI Semibold", 11))
        self.vol_lbl.pack(side="right")
        ttk.Scale(inner, from_=0, to=200, variable=self.vol, orient="horizontal",
                  style="Rev.Horizontal.TScale", command=self._on_vol).pack(
            fill="x", pady=(8, 4))
        ticks = tk.Frame(inner, bg=CARD)
        ticks.pack(fill="x")
        for t, side in (("0%", "left"), ("100%", None), ("200%", "right")):
            tk.Label(ticks, text=t, bg=CARD, fg="#5b5b6b",
                     font=("Segoe UI", 7)).pack(side=side or "left",
                                                expand=(side is None))
        pv = tk.Frame(inner, bg=CARD)
        pv.pack(fill="x", pady=(10, 0))
        self.preview_btn = tk.Button(pv, text="▶  Preview", command=self.preview,
                                     bg="#2a2a38", fg=TEXT, relief="flat",
                                     font=("Segoe UI", 9, "bold"), padx=16, pady=4,
                                     cursor="hand2", activebackground="#34344a")
        self.preview_btn.pack(side="left")
        self._hoverable(self.preview_btn, "#2a2a38", "#34344a")
        self.stop_btn = tk.Button(pv, text="■  Stop", command=self.stop_preview,
                                  bg="#2a2a38", fg=MUTED, relief="flat",
                                  font=("Segoe UI", 9, "bold"), padx=16, pady=4,
                                  cursor="hand2", state="disabled",
                                  activebackground="#34344a")
        self.stop_btn.pack(side="left", padx=(8, 0))

    def _build_actions(self, body):
        self._section(body, "INSTALL")
        self.btn = tk.Button(body, text="Build & Install Intro Song", command=self.run,
                             bg=GREEN, fg="white", relief="flat",
                             font=("Segoe UI Semibold", 13), pady=11, cursor="hand2",
                             activebackground="#1ea34d")
        self.btn.pack(fill="x")
        self._hoverable(self.btn, GREEN, "#2bd968")
        self.restore_btn = tk.Button(body, text="Restore Original Intro",
                                     command=self.restore, bg=CARD, fg=MUTED,
                                     relief="flat", font=("Segoe UI", 10, "bold"),
                                     pady=7, cursor="hand2", activebackground="#22222e",
                                     highlightbackground=BORDER, highlightthickness=1)
        self.restore_btn.pack(fill="x", pady=(8, 0))
        self._hoverable(self.restore_btn, CARD, "#22222e")

    def _build_log(self, body):
        self._section(body, "ACTIVITY")
        self.log_box = tk.Text(body, height=8, bg="#0a0a0e", fg="#8ad0ff",
                               font=("Consolas", 9), relief="flat", state="disabled",
                               highlightbackground=BORDER, highlightthickness=1,
                               padx=10, pady=8)
        self.log_box.pack(fill="both", expand=True)

    # ---- behavior ---------------------------------------------------------
    def _on_vol(self, _=None):
        pct = int(round(self.vol.get()))
        self.vol_lbl.config(text=f"{pct}%")
        # live: update gain instantly while preview is playing
        if self._player is not None and self._player.is_playing():
            self._player.set_gain(pct / 100.0)

    @staticmethod
    def _parse_time(s):
        s = (s or "").strip()
        if not s:
            return 0.0
        try:
            if ":" in s:
                mm, ss = s.split(":", 1)
                return int(mm or 0) * 60 + float(ss or 0)
            return float(s)
        except ValueError:
            return 0.0

    def _section_params(self):
        """Return (start_seconds, length_seconds) from the inputs, validated."""
        start = max(0.0, self._parse_time(self.start_str.get()))
        try:
            length = float((self.len_str.get() or "80").strip())
        except ValueError:
            length = 80.0
        length = max(5.0, length)
        return start, length

    def log(self, msg):
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")
        self.update_idletasks()

    def pick_audio(self):
        p = filedialog.askopenfilename(
            title="Choose your song",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac"),
                       ("All files", "*.*")])
        if not p:
            return
        self.audio_path.set(p)
        self.stop_preview()
        if HAVE_LIVE:
            self.wave.show_placeholder("Loading waveform...")
            threading.Thread(target=self._load_song_async, args=(p,), daemon=True).start()

    # ---- song loading + section helpers -----------------------------------
    def _ensure_song(self, audio):
        if self._song_src != audio or self._song is None:
            raw = decode_to_pcm(audio, 1.0)            # full song, raw
            self._song = np.frombuffer(raw, dtype=np.int16).reshape(-1, CH)
            self._song_src = audio
            self._total = len(self._song) / RATE
        return self._song

    def _load_song_async(self, audio):
        try:
            self._ensure_song(audio)
            _, length = self._section_params()
            length = min(length, self._total)
            self.after(0, lambda: self._song_loaded(length))
        except Exception as e:
            self.after(0, lambda: self.wave.show_placeholder("Couldn't read this song"))
            self.log("Load error: " + str(e))

    def _song_loaded(self, length):
        self.wave.set_song(self._song, self._total, length)
        self._sync = True
        self.start_str.set("0:00")
        self.len_str.set(str(int(round(self.wave.length))))
        self._sync = False
        self.log(f"Waveform ready ({self._total:.0f}s song). Drag to pick your section.")

    def _make_clip(self, start, length, volume=1.0):
        """Slice the cached song to start..start+length with fades (and optional gain)."""
        a = self._song
        s = int(start * RATE)
        e = min(len(a), s + int(length * RATE))
        clip = a[s:e].astype(np.float32)
        fi = min(len(clip), int(0.4 * RATE))
        if fi > 0:
            clip[:fi] *= np.linspace(0, 1, fi)[:, None]
        fo = min(len(clip), int(2.0 * RATE))
        if fo > 0:
            clip[-fo:] *= np.linspace(1, 0, fo)[:, None]
        if abs(volume - 1.0) > 1e-3:
            clip *= volume
        np.clip(clip, -32768, 32767, out=clip)
        return clip.astype(np.int16)

    def _on_wave_change(self, start, length):
        self._sync = True
        self.start_str.set(f"{int(start//60)}:{int(start%60):02d}")
        self.len_str.set(str(int(round(length))))
        self._sync = False
        if self._player is not None and self._player.is_playing():
            self._start_section_preview()      # re-cue instantly at new spot

    def _on_fields(self, *_):
        if self._sync or not hasattr(self, "wave") or self._song is None:
            return
        start, length = self._section_params()
        self._sync = True
        self.wave.set_length(length)
        self.wave.set_start(start)
        self._sync = False

    def _poll_playhead(self):
        if self._player is not None and self._player.is_playing():
            start, _ = self._section_params()
            try:
                self.wave.set_playhead(start + self._player.pos / RATE)
            except Exception:
                pass
            self.after(40, self._poll_playhead)
        else:
            try:
                self.wave.set_playhead(None)
            except Exception:
                pass

    def _busy(self, on, label="Working..."):
        state = "disabled" if on else "normal"
        self.btn.config(state=state,
                        text=label if on else "Build & Install Intro Song")
        self.restore_btn.config(state=state)
        self.preview_btn.config(state=state)

    # ---- preview ----------------------------------------------------------
    def preview(self):
        audio = self.audio_path.get().strip()
        if not audio or not os.path.exists(audio):
            messagebox.showerror("Missing song", "Please choose a valid audio file first.")
            return
        if self._player is None and winsound is None:
            messagebox.showinfo("Preview", "Audio preview isn't available on this system.")
            return
        self.preview_btn.config(state="disabled", text="Loading...")
        threading.Thread(target=self._preview_work, args=(audio,), daemon=True).start()

    def _start_section_preview(self):
        """Slice the chosen section from the cached song and loop it (live engine)."""
        gain = self.vol.get() / 100.0
        start, length = self._section_params()
        clip = self._make_clip(start, length)
        self._player.load(clip.tobytes())
        self._player.play(gain, loop=True)
        self._poll_playhead()
        self.stop_btn.config(state="normal", fg=TEXT)

    def _preview_work(self, audio):
        try:
            gain = self.vol.get() / 100.0
            if self._player is not None:                      # live engine, looped
                self._ensure_song(audio)
                if self.wave.env is None:
                    self.after(0, lambda: self._song_loaded(
                        min(self._section_params()[1], self._total)))
                self.after(0, self._start_section_preview)
                self.log("Playing section on loop - drag the window or slider live. "
                         "This is exactly how it loops in-game.")
            else:                                             # winsound fallback
                tmp = os.path.join(tempfile.gettempdir(), "revtoolz_preview.wav")
                start, length = self._section_params()
                make_preview_wav(audio, gain, tmp)
                self._preview_wav = tmp
                winsound.PlaySound(tmp, winsound.SND_FILENAME | winsound.SND_ASYNC)
                self.log("Playing preview (press Stop to end).")
                self.stop_btn.config(state="normal", fg=TEXT)
        except Exception as e:
            self.log("Preview error: " + str(e))
        finally:
            self.preview_btn.config(state="normal", text="▶  Preview")

    def _preview_ended(self):
        # called from the audio thread when playback finishes naturally
        try:
            self.after(0, lambda: self.stop_btn.config(state="disabled", fg=MUTED))
        except Exception:
            pass

    def stop_preview(self):
        if self._player is not None:
            self._player.stop()
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if hasattr(self, "wave"):
            try:
                self.wave.set_playhead(None)
            except Exception:
                pass
        self.stop_btn.config(state="disabled", fg=MUTED)

    # ---- install ----------------------------------------------------------
    def run(self):
        audio = self.audio_path.get().strip()
        if not audio or not os.path.exists(audio):
            messagebox.showerror("Missing song", "Please choose a valid audio file first.")
            return
        self.stop_preview()
        start, length = self._section_params()
        self._busy(True)
        threading.Thread(target=self._work,
                         args=(audio, self.vol.get() / 100.0, start, length),
                         daemon=True).start()

    def _choose_target(self, banks):
        if len(banks) == 1:
            return banks[0]
        win = tk.Toplevel(self)
        win.title("Choose game install")
        win.configure(bg=BG)
        try:
            win.iconbitmap(self._ico)
        except Exception:
            pass
        win.grab_set()
        tk.Label(win, text="Multiple game files found - pick the one to update:",
                 bg=BG, fg=TEXT, font=("Segoe UI", 10)).pack(padx=16, pady=12)
        lb = tk.Listbox(win, width=92, height=min(8, len(banks)), bg=CARD, fg=TEXT,
                        relief="flat", selectbackground=ACCENT)
        for b in banks:
            lb.insert("end", b)
        lb.select_set(0)
        lb.pack(padx=16, pady=6)
        choice = {"path": None}

        def ok():
            choice["path"] = banks[lb.curselection()[0]] if lb.curselection() else None
            win.destroy()

        tk.Button(win, text="Use this one", command=ok, bg=GREEN, fg="white",
                  relief="flat", font=("Segoe UI", 10, "bold"), padx=14, pady=5,
                  cursor="hand2").pack(pady=12)
        self.wait_window(win)
        return choice["path"]

    def _locate_or_pick(self):
        banks = find_banks(self.log)
        if not banks:
            self.log("Could not auto-find the game file.")
            t = filedialog.askopenfilename(
                title="Locate GLB_RadioPressStart.assets.bank",
                filetypes=[("FMOD bank", "*.bank"), ("All files", "*.*")])
            return t or None
        return self._choose_target(banks)

    def _work(self, audio, volume, start, length):
        try:
            if self._player is not None and self._song is not None:
                # build from the exact section you previewed (volume baked in)
                self.log(f"Using {length:g}s from {int(start//60)}:{int(start%60):02d} "
                         f"at {int(round(volume*100))}% volume...")
                clip = self._make_clip(start, length, volume)
                new_bytes = bank_from_section_pcm(clip.tobytes(), self.log)
            else:
                new_bytes = build_bank_bytes(audio, volume, self.log,
                                             start=start, length=length)
            target = self._locate_or_pick()
            if not target:
                self.log("Cancelled.")
                return
            self.log("Installing...")
            try:
                install_bank(new_bytes, target, self.log)
            except PermissionError:
                raise RuntimeError("Windows blocked writing to the game folder.\n\n"
                                   "Right-click this tool and 'Run as administrator'.")
            self.log("\nAll done!")
            messagebox.showinfo("Success",
                                "Your intro song is installed!\n\nA backup of the "
                                "original was saved as ...assets.bank.bak\n\n"
                                "Launch the game to hear it.")
        except Exception as e:
            self.log("ERROR: " + str(e))
            messagebox.showerror("Failed", str(e))
        finally:
            self._busy(False)

    # ---- restore ----------------------------------------------------------
    def restore(self):
        if not messagebox.askyesno("Restore original",
                                   "Put the game's ORIGINAL intro back from the backup, "
                                   "removing your custom one?"):
            return
        self.stop_preview()
        self._busy(True)
        threading.Thread(target=self._restore_work, daemon=True).start()

    def _restore_work(self):
        try:
            target = self._locate_or_pick()
            if not target:
                self.log("Cancelled.")
                return
            bak = target + ".bak"
            if not os.path.exists(bak):
                raise RuntimeError("No backup (.bak) found next to the game file.\n\n"
                                   "A backup is only created the first time you install "
                                   "a custom song with this tool.")
            self.log("Restoring original...")
            try:
                shutil.copy2(bak, target)
            except PermissionError:
                raise RuntimeError("Windows blocked writing to the game folder.\n\n"
                                   "Right-click this tool and 'Run as administrator'.")
            self.log(f"  restored -> {target}")
            messagebox.showinfo("Restored", "The original intro song has been put back.")
        except Exception as e:
            self.log("ERROR: " + str(e))
            messagebox.showerror("Failed", str(e))
        finally:
            self._busy(False)

    def _on_close(self):
        self.stop_preview()
        try:
            if self._preview_wav and os.path.exists(self._preview_wav):
                os.remove(self._preview_wav)
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
