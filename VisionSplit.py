import os
import re
import sys
import json
import time
import queue
import threading
import subprocess
from pathlib import Path
from typing import List, Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image

SETTINGS_FILE = Path("episode_encoder_settings.json")

def resource_path(relative_path: str) -> str:
    """
    Works in normal Python and PyInstaller builds.
    Put assets beside the script during development, and include them in PyInstaller.
    """
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(__file__).parent

    return str(base_path / relative_path)

import shutil

def resolve_ffmpeg_tools():
    # 1️⃣ If running as PyInstaller onefile build
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)
        ffmpeg_path = base_path / "ffmpeg.exe"
        ffprobe_path = base_path / "ffprobe.exe"

        if ffmpeg_path.exists() and ffprobe_path.exists():
            return str(ffmpeg_path), str(ffprobe_path)

    # 2️⃣ Check next to the exe (onedir builds)
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    ffmpeg_local = exe_dir / "ffmpeg.exe"
    ffprobe_local = exe_dir / "ffprobe.exe"

    if ffmpeg_local.exists() and ffprobe_local.exists():
        return str(ffmpeg_local), str(ffprobe_local)

    # 3️⃣ Fallback to system PATH
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe

    return None, None

_FFMPEG_PATH = None
_FFPROBE_PATH = None

def get_ffmpeg_tools():
    global _FFMPEG_PATH, _FFPROBE_PATH
    if _FFMPEG_PATH and _FFPROBE_PATH:
        return _FFMPEG_PATH, _FFPROBE_PATH

    ffmpeg, ffprobe = resolve_ffmpeg_tools()
    _FFMPEG_PATH, _FFPROBE_PATH = ffmpeg, ffprobe
    return ffmpeg, ffprobe

# ----------------------------
# Helpers: settings
# ----------------------------
def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_settings(d: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass


# ----------------------------
# Helpers: time parsing
# ----------------------------
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?\s*$")

def parse_hhmmss_to_ms(s: str) -> Optional[int]:
    m = _TIME_RE.match(s or "")
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    ss = int(m.group(3))
    ms = int((m.group(4) or "0").ljust(3, "0")[:3])
    return ((hh * 3600 + mm * 60 + ss) * 1000) + ms

def ms_to_ffmeta_time(ms: int) -> str:
    # ffmetadata chapter times use TIMEBASE=1/1000 so START/END are in ms integers
    return str(int(ms))

def ms_to_hhmmss(ms: int) -> str:
    total = int(ms // 1000)
    hh = total // 3600
    mm = (total % 3600) // 60
    ss = total % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"

def build_segments_from_starts(starts_ms: list[int], duration_ms: int) -> list[tuple[int, int]]:
    # Sanitize, sort, unique, and keep in range.
    starts = sorted({ms for ms in starts_ms if 0 <= ms < duration_ms})

    if not starts:
        return []

    # Always include the beginning of the video.
    # This matches the UI hint and prevents accidentally skipping episode 1.
    if 0 not in starts:
        starts.insert(0, 0)

    segs = []
    for i, s in enumerate(starts):
        e = duration_ms if i == len(starts) - 1 else starts[i + 1]
        if e > s:
            segs.append((s, e))

    return segs

# ----------------------------
# Helpers: ffprobe / ffmpeg
# ----------------------------
def have_ffmpeg() -> bool:
    ffmpeg, ffprobe = get_ffmpeg_tools()
    if not ffmpeg or not ffprobe:
        return False
    try:
        subprocess.check_output([ffmpeg, "-version"], stderr=subprocess.STDOUT, text=True, timeout=5)
        subprocess.check_output([ffprobe, "-version"], stderr=subprocess.STDOUT, text=True, timeout=5)
        return True
    except Exception:
        return False

def ffprobe_duration_ms(path: str) -> Optional[int]:
    ffmpeg, ffprobe = get_ffmpeg_tools()
    if not ffprobe:
        return None
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stderr=subprocess.STDOUT, text=True, timeout=10
        ).strip()
        if not out:
            return None
        dur_s = float(out)
        return int(round(dur_s * 1000.0))
    except Exception:
        return None

def ffprobe_chapter_starts_ms(path: str) -> List[int]:
    ffmpeg, ffprobe = get_ffmpeg_tools()
    if not ffprobe:
        return []
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_chapters", path],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15
        )
        data = json.loads(out)
        ch = data.get("chapters") or []
        starts_ms: List[int] = []
        for c in ch:
            st = c.get("start_time", None)
            if st is None:
                continue
            try:
                sec = float(st)
                ms = int(round(sec * 1000.0))
                if ms >= 0:
                    starts_ms.append(ms)
            except Exception:
                continue

        return sorted(set(starts_ms))
    except Exception:
        return []

def build_ffmetadata_chapters(chapter_starts_ms: List[int], duration_ms: int, title_prefix: str = "Chapter") -> str:
    """
    Builds ffmetadata text with chapters.
    Requires TIMEBASE 1/1000 and START/END in ms.
    """
    starts = sorted(set([ms for ms in chapter_starts_ms if 0 <= ms < duration_ms]))
    if 0 not in starts:
        starts.insert(0, 0)

    lines = [";FFMETADATA1"]
    for i, start in enumerate(starts):
        end = duration_ms if i == len(starts) - 1 else max(start + 1, starts[i + 1] - 1)
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={ms_to_ffmeta_time(start)}",
            f"END={ms_to_ffmeta_time(end)}",
            f"title={title_prefix} {i+1}",
        ]
    return "\n".join(lines) + "\n"

# ------------------------------------------------------------------
# Stage 1 UI helpers
# ------------------------------------------------------------------
def _card(self, parent, title: str = "", subtitle: str = "", **grid_kwargs):
    """
    Reusable futuristic card helper.
    Keeps the UI consistent and makes future stages easier.
    """
    card = ctk.CTkFrame(
        parent,
        fg_color=self.ui["panel"],
        corner_radius=18,
        border_width=1,
        border_color=self.ui["border"],
    )

    if grid_kwargs:
        card.grid(**grid_kwargs)

    if title:
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 8))

        ctk.CTkLabel(
            header,
            text=title,
            anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=self.ui["text"],
        ).pack(fill="x")

        if subtitle:
            ctk.CTkLabel(
                header,
                text=subtitle,
                anchor="w",
                justify="left",
                wraplength=420,
                font=ctk.CTkFont(size=12),
                text_color=self.ui["muted"],
            ).pack(fill="x", pady=(3, 0))

    return card

def _subcard(self, parent, title: str = ""):
    frame = ctk.CTkFrame(
        parent,
        fg_color=self.ui["panel_2"],
        corner_radius=15,
        border_width=1,
        border_color=self.ui["border"],
    )
    if title:
        ctk.CTkLabel(
            frame,
            text=title,
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=self.ui["text"],
        ).pack(fill="x", padx=12, pady=(12, 6))
    return frame

def _grid_card(self, parent, title: str = "", subtitle: str = "", **grid_kwargs):
    """
    Grid-safe card helper.
    Use this when the direct children inside the card will use .grid().
    """
    card = ctk.CTkFrame(
        parent,
        fg_color=self.ui["panel"],
        corner_radius=18,
        border_width=1,
        border_color=self.ui["border"],
    )

    if grid_kwargs:
        card.grid(**grid_kwargs)

    card.grid_columnconfigure(0, weight=1)
    card.grid_columnconfigure(1, weight=1)

    if title:
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 8))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=title,
            anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, sticky="ew")

        if subtitle:
            ctk.CTkLabel(
                header,
                text=subtitle,
                anchor="w",
                justify="left",
                wraplength=420,
                font=ctk.CTkFont(size=12),
                text_color=self.ui["muted"],
            ).grid(row=1, column=0, sticky="ew", pady=(3, 0))

    return card


def _grid_subcard(self, parent, title: str = ""):
    """
    Grid-safe subcard helper.
    Use this when the direct children inside the subcard will use .grid().
    """
    frame = ctk.CTkFrame(
        parent,
        fg_color=self.ui["panel_2"],
        corner_radius=15,
        border_width=1,
        border_color=self.ui["border"],
    )

    frame.grid_columnconfigure(0, weight=1)
    frame.grid_columnconfigure(1, weight=1)
    frame.grid_columnconfigure(2, weight=1)
    frame.grid_columnconfigure(3, weight=1)

    if title:
        ctk.CTkLabel(
            frame,
            text=title,
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(12, 6))

    return frame


def _path_row(self, parent, label: str, entry_attr: str, placeholder: str, command):
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=16, pady=6)
    row.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(
        row,
        text=label,
        width=105,
        anchor="w",
        text_color=self.ui["muted"],
        font=ctk.CTkFont(size=13, weight="bold"),
    ).grid(row=0, column=0, padx=(0, 10), sticky="w")

    entry = ctk.CTkEntry(
        row,
        placeholder_text=placeholder,
        fg_color=self.ui["entry"],
        border_color=self.ui["border"],
        height=38,
    )
    entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))

    btn = ctk.CTkButton(
        row,
        text="Browse",
        width=92,
        height=38,
        command=command,
        fg_color=self.ui["soft_button"],
        hover_color=self.ui["soft_button_hover"],
    )
    btn.grid(row=0, column=2)

    setattr(self, entry_attr, entry)
    return entry

def _grid_label(self, parent, text, row, column=0):
    ctk.CTkLabel(
        parent,
        text=text,
        anchor="w",
        text_color=self.ui["muted"],
        font=ctk.CTkFont(size=13, weight="bold"),
    ).grid(row=row, column=column, padx=(14, 8), pady=6, sticky="w")

def _grid_entry(self, parent, row, placeholder="", value="", column=1, width=None):
    entry_kwargs = {
        "placeholder_text": placeholder,
        "fg_color": self.ui["entry"],
        "border_color": self.ui["border"],
        "height": 36,
    }

    # Important:
    # Some CustomTkinter versions crash if width=None is passed.
    # Only pass width when it is an actual number.
    if width is not None:
        entry_kwargs["width"] = width

    entry = ctk.CTkEntry(parent, **entry_kwargs)
    entry.grid(row=row, column=column, padx=(8, 14), pady=6, sticky="ew")

    if value != "":
        entry.insert(0, str(value))

    return entry
def _grid_option(self, parent, row, values, value):
    opt = ctk.CTkOptionMenu(
        parent,
        values=values,
        height=36,
        fg_color=self.ui["soft_button"],
        button_color=self.ui["button"],
        button_hover_color=self.ui["button_hover"],
        dropdown_fg_color=self.ui["panel_2"],
        dropdown_hover_color=self.ui["soft_button_hover"],
    )
    opt.grid(row=row, column=1, padx=(8, 14), pady=6, sticky="ew")
    opt.set(value)
    return opt

def _pill_button(self, parent, text, command=None, width=92, danger=False):
    return ctk.CTkButton(
        parent,
        text=text,
        width=width,
        height=34,
        command=command,
        fg_color=self.ui["danger"] if danger else self.ui["soft_button"],
        hover_color=self.ui["danger_hover"] if danger else self.ui["soft_button_hover"],
        corner_radius=12,
    )



# ----------------------------
# UI App
# ----------------------------
class EpisodeEncoderApp(ctk.CTk):
    # Bind the Stage 1 helper functions as class methods.
    _card = _card
    _subcard = _subcard
    _grid_card = _grid_card
    _grid_subcard = _grid_subcard
    _path_row = _path_row
    _grid_label = _grid_label
    _grid_entry = _grid_entry
    _grid_option = _grid_option
    _pill_button = _pill_button

    def __init__(self):
        super().__init__()
        self.title("VisionSplit - Split and Stitch")
        try:
            self.iconbitmap(resource_path("VisionSplit.ico"))
        except Exception:
            pass
        self.settings = load_settings()

        # Stage 1: larger premium default window, but still scroll-safe on smaller screens.
        self.geometry(self.settings.get("window_geometry", "1360x900"))
        self.minsize(1080, 720)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        ctk.set_appearance_mode(self.settings.get("appearance_mode", "Dark"))
        ctk.set_default_color_theme("blue")

        # Futuristic dark UI palette used by the rebuilt layout.
        self.ui = {
            "bg": "#070A12",
            "root": "#090D18",
            "panel": "#0D1424",
            "panel_2": "#111A2D",
            "panel_3": "#0A1020",
            "border": "#1E2A44",
            "border_hot": "#2E9BFF",
            "text": "#EAF2FF",
            "muted": "#8FA2C4",
            "muted_2": "#64718A",
            "accent": "#39C6FF",
            "accent_2": "#7CFFCB",
            "danger": "#B3264B",
            "danger_hover": "#8F1D3B",
            "entry": "#090F1D",
            "button": "#1B6CFF",
            "button_hover": "#1557CC",
            "soft_button": "#152036",
            "soft_button_hover": "#1E2C49",
        }

        self.configure(fg_color=self.ui["bg"])

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.stitch_clips: List[str] = []

        self._build_ui()
        self.after(100, self._drain_ui_queue)

    def _build_ui(self):
        # Main root shell
        shell = ctk.CTkFrame(self, fg_color=self.ui["root"], corner_radius=0)
        shell.pack(fill="both", expand=True)

        shell.grid_rowconfigure(2, weight=1)
        shell.grid_columnconfigure(0, weight=1)

        # ------------------------------------------------------------------
        # Header / brand bar
        # ------------------------------------------------------------------
        header = ctk.CTkFrame(shell, fg_color=self.ui["bg"], corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=20, pady=16)

        logo = ctk.CTkFrame(
            brand,
            width=52,
            height=52,
            fg_color="#0E1A2E",
            corner_radius=16,
            border_width=1,
            border_color="#24476D",
        )
        logo.pack(side="left", padx=(0, 12))
        logo.pack_propagate(False)

        try:
            self.logo_image = ctk.CTkImage(
                light_image=Image.open(resource_path("VisionSplitLogo.png")),
                dark_image=Image.open(resource_path("VisionSplitLogo.png")),
                size=(38, 38),
            )

            ctk.CTkLabel(
                logo,
                text="",
                image=self.logo_image,
            ).pack(expand=True)

        except Exception:
            ctk.CTkLabel(
                logo,
                text="VS",
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color=self.ui["accent"],
            ).pack(expand=True)

        brand_text = ctk.CTkFrame(brand, fg_color="transparent")
        brand_text.pack(side="left")

        ctk.CTkLabel(
            brand_text,
            text="VisionSplit",
            anchor="w",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=self.ui["text"],
        ).pack(fill="x")

        ctk.CTkLabel(
            brand_text,
            text="Split episodes • export clips • stitch media",
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color=self.ui["muted"],
        ).pack(fill="x", pady=(1, 0))

        header_status = ctk.CTkFrame(header, fg_color="transparent")
        header_status.grid(row=0, column=1, sticky="e", padx=20, pady=16)

        ctk.CTkLabel(
            header_status,
            text="FFmpeg-powered local workflow",
            text_color=self.ui["muted"],
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="e")

        ctk.CTkLabel(
            header_status,
            text="No cloud processing. Your files stay on your machine.",
            text_color=self.ui["muted_2"],
            font=ctk.CTkFont(size=12),
        ).pack(anchor="e", pady=(2, 0))

        # ------------------------------------------------------------------
        # Top path card
        # ------------------------------------------------------------------
        path_card = self._card(
            shell,
            title="Source and destination",
            subtitle="Choose the input video and output folder used by split, clip, and stitch operations."
        )
        path_card.grid(row=1, column=0, sticky="ew", padx=18, pady=(14, 10))

        self._path_row(
            path_card,
            "Input file",
            "in_entry",
            "Pick an episode video file...",
            self.pick_input,
        )
        self._path_row(
            path_card,
            "Output folder",
            "out_entry",
            "Pick an output folder...",
            self.pick_output,
        )

        # Restore last paths
        if self.settings.get("last_input"):
            self.in_entry.insert(0, self.settings["last_input"])
        if self.settings.get("last_output"):
            self.out_entry.insert(0, self.settings["last_output"])

        # ------------------------------------------------------------------
        # Scroll-safe main content area
        # ------------------------------------------------------------------
        main = ctk.CTkScrollableFrame(
            shell,
            fg_color=self.ui["root"],
            corner_radius=0,
            scrollbar_button_color=self.ui["soft_button"],
            scrollbar_button_hover_color=self.ui["button"],
        )
        main.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 10))

        main.grid_columnconfigure(0, weight=1, uniform="maincols")
        main.grid_columnconfigure(1, weight=1, uniform="maincols")
        main.grid_columnconfigure(2, weight=1, uniform="maincols")

        # ==================================================================
        # LEFT PANEL: timestamps, smart split, stitcher
        # ==================================================================
        left_col = ctk.CTkFrame(main, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8)
        left_col.grid_columnconfigure(0, weight=1)

        ts_frame = self._card(
            left_col,
            title="Episode split points",
            subtitle="Add timestamps manually, load chapters, or generate episode starts automatically."
        )
        ts_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 12))

        ts_row = ctk.CTkFrame(ts_frame, fg_color="transparent")
        ts_row.pack(fill="x", padx=16, pady=(4, 10))
        ts_row.grid_columnconfigure(0, weight=1)

        self.ts_entry = ctk.CTkEntry(
            ts_row,
            placeholder_text="HH:MM:SS  example: 00:12:34",
            fg_color=self.ui["entry"],
            border_color=self.ui["border"],
            height=38,
        )
        self.ts_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self._pill_button(ts_row, "+ Add", self.add_timestamp, width=70).grid(row=0, column=1, padx=(0, 6))
        self._pill_button(ts_row, "Chapters", self.load_chapters_into_timestamps, width=88).grid(row=0, column=2, padx=(0, 6))
        self._pill_button(ts_row, "Clear", self.clear_timestamps, width=70).grid(row=0, column=3)

        self.ts_list = ctk.CTkTextbox(
            ts_frame,
            wrap="none",
            height=210,
            fg_color=self.ui["entry"],
            border_width=1,
            border_color=self.ui["border"],
            text_color=self.ui["text"],
        )
        self.ts_list.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        # Keep existing timestamp editing behavior.
        self.ts_list.bind("<Control-Delete>", self._on_ts_delete_line_hotkey)

        del_row = ctk.CTkFrame(ts_frame, fg_color="transparent")
        del_row.pack(fill="x", padx=16, pady=(0, 10))

        self._pill_button(
            del_row,
            "Delete selected line",
            self.delete_selected_timestamp,
            width=160
        ).pack(side="left")

        ctk.CTkLabel(
            ts_frame,
            text="Tip: 00:00:00 is added automatically if it is missing.",
            anchor="w",
            text_color=self.ui["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=16, pady=(0, 14))

        # Smart split
        smart_frame = self._grid_subcard(ts_frame, "Smart TV / Anime split")
        smart_frame.pack(fill="x", padx=16, pady=(0, 16))
        smart_frame.grid_columnconfigure(1, weight=1)
        smart_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(smart_frame, text="Episode length", text_color=self.ui["muted"], anchor="w").grid(
            row=1, column=0, padx=(12, 6), pady=6, sticky="w"
        )
        self.smart_ep_len_entry = ctk.CTkEntry(
            smart_frame,
            placeholder_text="00:23:40",
            width=105,
            fg_color=self.ui["entry"],
            border_color=self.ui["border"],
        )
        self.smart_ep_len_entry.grid(row=1, column=1, padx=(6, 8), pady=6, sticky="ew")
        self.smart_ep_len_entry.insert(0, str(self.settings.get("smart_ep_len", "00:23:40")))

        ctk.CTkLabel(smart_frame, text="Count", text_color=self.ui["muted"], anchor="w").grid(
            row=1, column=2, padx=(6, 6), pady=6, sticky="w"
        )
        self.smart_ep_count_entry = ctk.CTkEntry(
            smart_frame,
            placeholder_text="6",
            width=70,
            fg_color=self.ui["entry"],
            border_color=self.ui["border"],
        )
        self.smart_ep_count_entry.grid(row=1, column=3, padx=(6, 12), pady=6, sticky="ew")
        self.smart_ep_count_entry.insert(0, str(self.settings.get("smart_ep_count", "4")))

        self._pill_button(
            smart_frame,
            "Generate",
            self.generate_timestamps_by_episode_length,
            width=100
        ).grid(row=2, column=0, columnspan=4, padx=12, pady=(6, 8), sticky="ew")

        chapter_row = ctk.CTkFrame(smart_frame, fg_color="transparent")
        chapter_row.grid(row=3, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 12))
        chapter_row.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(chapter_row, text="Every", text_color=self.ui["muted"]).grid(row=0, column=0, padx=(0, 6))
        self.chapter_every_entry = ctk.CTkEntry(
            chapter_row,
            placeholder_text="5",
            width=48,
            fg_color=self.ui["entry"],
            border_color=self.ui["border"],
        )
        self.chapter_every_entry.grid(row=0, column=1, padx=(0, 8))
        self.chapter_every_entry.insert(0, str(self.settings.get("chapter_every", "5")))

        ctk.CTkLabel(chapter_row, text="chapter(s), offset", text_color=self.ui["muted"]).grid(row=0, column=2, padx=(0, 6))

        self.chapter_offset_entry = ctk.CTkEntry(
            chapter_row,
            placeholder_text="0",
            width=48,
            fg_color=self.ui["entry"],
            border_color=self.ui["border"],
        )
        self.chapter_offset_entry.grid(row=0, column=3, padx=(0, 8))
        self.chapter_offset_entry.insert(0, str(self.settings.get("chapter_offset", "0")))

        self._pill_button(
            chapter_row,
            "Use Chapters",
            self.load_every_nth_chapter_into_timestamps,
            width=112
        ).grid(row=0, column=4, padx=(0, 8))

        self._pill_button(
            chapter_row,
            "Preview",
            self.preview_split_plan,
            width=84
        ).grid(row=0, column=5, sticky="e")

        # Clip stitcher
        stitch_frame = self._card(
            left_col,
            title="Clip stitcher",
            subtitle="Build a clip list and stitch selected files into a single output."
        )
        stitch_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 0))

        stitch_btns = ctk.CTkFrame(stitch_frame, fg_color="transparent")
        stitch_btns.pack(fill="x", padx=16, pady=(4, 10))

        self._pill_button(stitch_btns, "+ Add clips", self.add_stitch_clips, width=96).pack(side="left", padx=(0, 6), pady=2)
        self._pill_button(stitch_btns, "Remove", self.remove_selected_stitch_clip, width=82).pack(side="left", padx=(0, 6), pady=2)
        self._pill_button(stitch_btns, "Up", self.move_stitch_clip_up, width=55).pack(side="left", padx=(0, 6), pady=2)
        self._pill_button(stitch_btns, "Down", self.move_stitch_clip_down, width=65).pack(side="left", padx=(0, 6), pady=2)
        self._pill_button(stitch_btns, "Clear", self.clear_stitch_clips, width=62).pack(side="left", pady=2)

        self.stitch_list = ctk.CTkTextbox(
            stitch_frame,
            wrap="none",
            height=125,
            fg_color=self.ui["entry"],
            border_width=1,
            border_color=self.ui["border"],
            text_color=self.ui["text"],
        )
        self.stitch_list.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        # ==================================================================
        # CENTER PANEL: encoder settings
        # ==================================================================
        center_col = ctk.CTkFrame(main, fg_color="transparent")
        center_col.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        center_col.grid_columnconfigure(0, weight=1)

        enc = self._grid_card(
            center_col,
            title="Encoder settings",
            subtitle="Choose container, video/audio codecs, quality, subtitles, and output naming."
        )
        enc.grid(row=0, column=0, sticky="nsew")
        enc.grid_columnconfigure(1, weight=1)

        self._grid_label(enc, "Container", 1)
        self.container_opt = self._grid_option(enc, 1, ["mkv", "mp4"], self.settings.get("container", "mkv"))

        self._grid_label(enc, "Video codec", 2)
        self.vcodec_opt = self._grid_option(
            enc,
            2,
            ["libx264", "libx265", "h264_nvenc", "hevc_nvenc", "copy"],
            self.settings.get("vcodec", "libx264"),
        )

        def _on_vcodec_change(choice: str):
            # NVENC uses CQ, not CRF. The same entry is reused safely.
            if choice in ("h264_nvenc", "hevc_nvenc"):
                cur = (self.crf_entry.get() or "").strip()
                if not cur:
                    self.crf_entry.insert(0, "20")

        self.vcodec_opt.configure(command=_on_vcodec_change)

        self._grid_label(enc, "Preset", 3)
        self.preset_opt = self._grid_option(
            enc,
            3,
            [
                "ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
                "slow", "slower", "veryslow", "p1", "p2", "p3", "p4", "p5", "p6", "p7"
            ],
            self.settings.get("preset", "medium"),
        )

        self._grid_label(enc, "CRF / CQ", 4)
        self.crf_entry = self._grid_entry(
            enc,
            4,
            placeholder="CRF for x264/x265 or CQ for NVENC",
            value=str(self.settings.get("crf", 20)),
        )

        self._grid_label(enc, "Audio", 5)
        self.acodec_opt = self._grid_option(enc, 5, ["aac", "copy"], self.settings.get("acodec", "aac"))

        self._grid_label(enc, "Audio bitrate", 6)
        self.abitrate_entry = self._grid_entry(
            enc,
            6,
            placeholder="e.g. 192k",
            value=str(self.settings.get("abitrate", "192k")),
        )

        self._grid_label(enc, "Show title", 7)
        self.show_title_entry = self._grid_entry(
            enc,
            7,
            placeholder="e.g. Pokémon the Series XYZ",
            value=str(self.settings.get("show_title", "")),
        )

        self._grid_label(enc, "Season", 8)
        self.season_entry = self._grid_entry(
            enc,
            8,
            placeholder="e.g. 19",
            value=str(self.settings.get("season", "1")),
        )

        self._grid_label(enc, "Start EP #", 9)
        self.start_ep_entry = self._grid_entry(
            enc,
            9,
            placeholder="e.g. 1",
            value=str(self.settings.get("start_ep", "1")),
        )

        self.fast_split_var = ctk.BooleanVar(value=bool(self.settings.get("fast_split", True)))
        self.include_subs_var = ctk.BooleanVar(value=bool(self.settings.get("include_subs", True)))

        checks = ctk.CTkFrame(enc, fg_color="transparent")
        checks.grid(row=10, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 14))

        ctk.CTkCheckBox(
            checks,
            text="Fast split copy mode",
            variable=self.fast_split_var,
            text_color=self.ui["text"],
            fg_color=self.ui["button"],
            hover_color=self.ui["button_hover"],
            border_color=self.ui["border"],
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkCheckBox(
            checks,
            text="Include subtitle tracks",
            variable=self.include_subs_var,
            text_color=self.ui["text"],
            fg_color=self.ui["button"],
            hover_color=self.ui["button_hover"],
            border_color=self.ui["border"],
        ).pack(anchor="w")

        note = ctk.CTkFrame(
            enc,
            fg_color=self.ui["panel_3"],
            corner_radius=14,
            border_width=1,
            border_color=self.ui["border"],
        )
        note.grid(row=11, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 16))

        ctk.CTkLabel(
            note,
            text="Fast split uses stream copy for maximum speed. Disable it when you want CRF/CQ quality settings to re-encode the output.",
            wraplength=420,
            justify="left",
            text_color=self.ui["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=12, pady=12)

        # ==================================================================
        # RIGHT PANEL: single clip export and command center
        # ==================================================================
        right_col = ctk.CTkFrame(main, fg_color="transparent")
        right_col.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=8)
        right_col.grid_columnconfigure(0, weight=1)

        clip_frame = self._grid_card(
            right_col,
            title="Single clip export",
            subtitle="Export a precise clip range from the selected input video."
        )
        clip_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        clip_frame.grid_columnconfigure(1, weight=1)

        self._grid_label(clip_frame, "Clip start", 1)
        self.clip_start_entry = self._grid_entry(
            clip_frame,
            1,
            placeholder="00:12:30",
            value=str(self.settings.get("clip_start", "00:00:00")),
        )

        self._grid_label(clip_frame, "Clip end", 2)
        self.clip_end_entry = self._grid_entry(
            clip_frame,
            2,
            placeholder="00:14:45",
            value=str(self.settings.get("clip_end", "00:01:00")),
        )

        self._grid_label(clip_frame, "Clip name", 3)
        self.clip_name_entry = self._grid_entry(
            clip_frame,
            3,
            placeholder="My Movie Clip",
            value=str(self.settings.get("clip_name", "")),
        )

        self.single_clip_btn = ctk.CTkButton(
            clip_frame,
            text="Export Single Clip",
            height=42,
            command=self.start_single_clip_export,
            fg_color=self.ui["button"],
            hover_color=self.ui["button_hover"],
            corner_radius=14,
        )
        self.single_clip_btn.grid(row=4, column=0, columnspan=2, padx=14, pady=(12, 16), sticky="ew")

        # ==================================================================
        # Stereo SBS tools
        # ==================================================================
        stereo_frame = self._grid_card(
            right_col,
            title="Stereo SBS tools",
            subtitle="Split Full SBS into separate eyes, or merge left/right eyes back into Full SBS."
        )
        stereo_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        stereo_frame.grid_columnconfigure(1, weight=1)
        stereo_frame.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(
            stereo_frame,
            text="Output name",
            anchor="w",
            text_color=self.ui["muted"],
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        self.stereo_name_entry = ctk.CTkEntry(
            stereo_frame,
            placeholder_text="Optional custom name",
            fg_color=self.ui["entry"],
            border_color=self.ui["border"],
            height=36,
        )
        self.stereo_name_entry.grid(row=1, column=1, columnspan=2, padx=(8, 14), pady=6, sticky="ew")
        self.stereo_name_entry.insert(0, str(self.settings.get("stereo_name", "")))

        ctk.CTkLabel(
            stereo_frame,
            text="Left eye",
            anchor="w",
            text_color=self.ui["muted"],
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=2, column=0, padx=(14, 8), pady=6, sticky="w")

        self.eye_left_entry = ctk.CTkEntry(
            stereo_frame,
            placeholder_text="Pick left eye video for merging...",
            fg_color=self.ui["entry"],
            border_color=self.ui["border"],
            height=36,
        )
        self.eye_left_entry.grid(row=2, column=1, padx=(8, 8), pady=6, sticky="ew")
        self.eye_left_entry.insert(0, str(self.settings.get("eye_left", "")))

        self._pill_button(
            stereo_frame,
            "Browse",
            self.pick_left_eye,
            width=80
        ).grid(row=2, column=2, padx=(0, 14), pady=6, sticky="e")

        ctk.CTkLabel(
            stereo_frame,
            text="Right eye",
            anchor="w",
            text_color=self.ui["muted"],
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=3, column=0, padx=(14, 8), pady=6, sticky="w")

        self.eye_right_entry = ctk.CTkEntry(
            stereo_frame,
            placeholder_text="Pick right eye video for merging...",
            fg_color=self.ui["entry"],
            border_color=self.ui["border"],
            height=36,
        )
        self.eye_right_entry.grid(row=3, column=1, padx=(8, 8), pady=6, sticky="ew")
        self.eye_right_entry.insert(0, str(self.settings.get("eye_right", "")))

        self._pill_button(
            stereo_frame,
            "Browse",
            self.pick_right_eye,
            width=80
        ).grid(row=3, column=2, padx=(0, 14), pady=6, sticky="e")

        self.sbs_split_btn = ctk.CTkButton(
            stereo_frame,
            text="Split Main Full SBS into Left + Right",
            height=40,
            command=self.start_split_full_sbs,
            fg_color=self.ui["soft_button"],
            hover_color=self.ui["soft_button_hover"],
            corner_radius=14,
        )
        self.sbs_split_btn.grid(row=4, column=0, columnspan=3, padx=14, pady=(12, 8), sticky="ew")

        self.sbs_merge_btn = ctk.CTkButton(
            stereo_frame,
            text="Merge Left + Right into Full SBS",
            height=40,
            command=self.start_merge_eyes_to_full_sbs,
            fg_color=self.ui["button"],
            hover_color=self.ui["button_hover"],
            corner_radius=14,
        )
        self.sbs_merge_btn.grid(row=5, column=0, columnspan=3, padx=14, pady=(0, 16), sticky="ew")

        actions = self._card(
            right_col,
            title="Command center",
            subtitle="Start split encoding, stitch clips, or stop the active FFmpeg job."
        )
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        actions.grid_columnconfigure(0, weight=1)

        self.start_btn = ctk.CTkButton(
            actions,
            text="Start Encode",
            height=46,
            command=self.start_encode,
            fg_color=self.ui["button"],
            hover_color=self.ui["button_hover"],
            corner_radius=14,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.start_btn.pack(fill="x", padx=16, pady=(6, 8))

        self.stitch_btn = ctk.CTkButton(
            actions,
            text="Start Stitch",
            height=42,
            command=self.start_stitch,
            fg_color=self.ui["soft_button"],
            hover_color=self.ui["soft_button_hover"],
            corner_radius=14,
        )
        self.stitch_btn.pack(fill="x", padx=16, pady=(0, 8))

        self.stop_btn = ctk.CTkButton(
            actions,
            text="Stop Active Job",
            height=42,
            fg_color=self.ui["danger"],
            hover_color=self.ui["danger_hover"],
            command=self.stop_encode,
            state="disabled",
            corner_radius=14,
        )
        self.stop_btn.pack(fill="x", padx=16, pady=(0, 16))

        info_card = self._card(
            right_col,
            title="Output naming",
            subtitle="Split exports use: Show Title - S##E##.container"
        )
        info_card.grid(row=3, column=0, sticky="ew", pady=(12, 0))

        ctk.CTkLabel(
            info_card,
            text="Example: My Show - S01E01.mkv\nUse Preview before encoding to inspect generated filenames and segment lengths.",
            justify="left",
            anchor="w",
            text_color=self.ui["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=16, pady=(4, 16))

        # ------------------------------------------------------------------
        # Bottom terminal log area with always-visible render status
        # ------------------------------------------------------------------
        log_panel = ctk.CTkFrame(
            shell,
            fg_color=self.ui["panel"],
            corner_radius=18,
            border_width=1,
            border_color=self.ui["border"],
        )
        log_panel.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        log_panel.grid_columnconfigure(0, weight=1)

        # Render status strip
        status_strip = ctk.CTkFrame(
            log_panel,
            fg_color=self.ui["panel_3"],
            corner_radius=14,
            border_width=1,
            border_color=self.ui["border"],
        )
        status_strip.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        status_strip.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            status_strip,
            text="Render Status",
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, sticky="w", padx=(12, 10), pady=(10, 4))

        self.status_lbl = ctk.CTkLabel(
            status_strip,
            text="Ready.",
            anchor="w",
            justify="left",
            wraplength=1200,
            text_color=self.ui["muted"],
            font=ctk.CTkFont(size=12),
        )
        self.status_lbl.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 4))

        self.progress = ctk.CTkProgressBar(
            status_strip,
            height=12,
            progress_color=self.ui["accent"],
            fg_color=self.ui["entry"],
        )
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 12))
        self.progress.set(0)

        # Terminal log header
        log_header = ctk.CTkFrame(log_panel, fg_color="transparent")
        log_header.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 6))
        log_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            log_header,
            text="Terminal Log",
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            log_header,
            text="FFmpeg output / warnings / preview reports",
            anchor="e",
            font=ctk.CTkFont(size=12),
            text_color=self.ui["muted_2"],
        ).grid(row=0, column=1, sticky="e")

        self.log_box = ctk.CTkTextbox(
            log_panel,
            height=120,
            fg_color="#050914",
            border_width=1,
            border_color=self.ui["border"],
            text_color="#CFE6FF",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_box.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        self.log_box.configure(state="disabled")

    def _on_ts_delete_line_hotkey(self, event=None):
        # Ctrl + Delete removes full timestamp lines.
        # Normal Backspace/Delete are left alone for text editing.
        if self._delete_selected_lines_if_any():
            return "break"

        self.delete_selected_timestamp()
        return "break"

    def _delete_selected_lines_if_any(self) -> bool:
        """
        If there is a selection in the timestamp textbox, delete ALL lines touched by it.
        Returns True if something was deleted.
        """
        try:
            sel_first = self.ts_list.index("sel.first")
            sel_last = self.ts_list.index("sel.last")
        except Exception:
            return False  # no selection

        # Convert indices like "5.0" into line numbers
        start_line = int(str(sel_first).split(".")[0])
        end_line = int(str(sel_last).split(".")[0])
        end_col = int(str(sel_last).split(".")[1])

        # If selection ends exactly at the start of a line, don't delete that next line
        if end_col == 0 and end_line > start_line:
            end_line -= 1

        lines = self._get_timestamp_lines()
        if not lines:
            return False

        # Clamp bounds
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines) - 1, end_line - 1)

        if start_idx > end_idx:
            return False

        # Delete the range
        del lines[start_idx:end_idx + 1]
        self._set_timestamp_lines(lines)
        return True

    # ----------------------------
    # Chapter Ops
    # ----------------------------

    def load_chapters_into_timestamps(self):
        if not have_ffmpeg():
            messagebox.showerror("ffmpeg missing", "ffmpeg/ffprobe not found in PATH.\nInstall ffmpeg and restart.")
            return

        in_path = (self.in_entry.get() or "").strip()
        if not in_path or not os.path.exists(in_path):
            messagebox.showerror("Input", "Pick a valid input file first.")
            return

        starts_ms = ffprobe_chapter_starts_ms(in_path)
        if not starts_ms:
            messagebox.showinfo("Chapters", "No chapters found in this file.")
            return

        # Convert to HH:MM:SS
        chapter_lines = [ms_to_hhmmss(ms) for ms in starts_ms]

        existing = self._get_timestamp_lines()
        if existing:
            ok = messagebox.askyesno(
                "Replace timestamps?",
                f"Found {len(chapter_lines)} chapters.\n\n"
                f"Replace your current {len(existing)} timestamp(s) with the chapter list?"
            )
            if not ok:
                return

        self._set_timestamp_lines(chapter_lines)
        self._log(f"Loaded {len(chapter_lines)} chapters into the timestamp list.")

    def clear_timestamps(self):
        existing = self._get_timestamp_lines()
        if not existing:
            return

        ok = messagebox.askyesno(
            "Clear timestamps?",
            f"Clear all {len(existing)} timestamp(s) from the list?"
        )
        if not ok:
            return

        self._set_timestamp_lines([])
        self._log("Cleared timestamp list.")

    # ----------------------------
    # Smart TV / Anime split tools
    # ----------------------------
    def _get_valid_input_duration(self):
        if not have_ffmpeg():
            messagebox.showerror(
                "ffmpeg missing",
                "ffmpeg/ffprobe not found.\n\nPlace ffmpeg.exe and ffprobe.exe beside VisionSplit.exe or add FFmpeg to PATH."
            )
            return None, None

        in_path = (self.in_entry.get() or "").strip()
        if not in_path or not os.path.exists(in_path):
            messagebox.showerror("Input", "Pick a valid input file first.")
            return None, None

        duration_ms = ffprobe_duration_ms(in_path)
        if not duration_ms:
            messagebox.showerror("ffprobe", "Could not read duration from input.")
            return None, None

        return in_path, duration_ms

    def _replace_timestamps_with_confirm(self, new_lines: List[str], source_name: str) -> bool:
        if not new_lines:
            messagebox.showerror("Timestamps", "No timestamps were generated.")
            return False

        existing = self._get_timestamp_lines()
        if existing:
            ok = messagebox.askyesno(
                "Replace timestamps?",
                f"{source_name} generated {len(new_lines)} timestamp(s).\n\n"
                f"Replace your current {len(existing)} timestamp(s)?"
            )
            if not ok:
                return False

        self._set_timestamp_lines(new_lines)
        self._log(f"{source_name}: loaded {len(new_lines)} timestamp(s).")
        return True

    def generate_timestamps_by_episode_length(self):
        in_path, duration_ms = self._get_valid_input_duration()
        if not in_path or not duration_ms:
            return

        ep_len_txt = (self.smart_ep_len_entry.get() or "").strip()
        ep_len_ms = parse_hhmmss_to_ms(ep_len_txt)

        if ep_len_ms is None or ep_len_ms <= 0:
            messagebox.showerror(
                "Episode length",
                "Episode length must be HH:MM:SS.\n\nExample: 00:23:40"
            )
            return

        try:
            ep_count = int((self.smart_ep_count_entry.get() or "0").strip())
        except Exception:
            ep_count = 0

        if ep_count <= 0:
            messagebox.showerror("Episode count", "Episode count must be a number greater than 0.")
            return

        starts_ms = []
        for i in range(ep_count):
            start_ms = i * ep_len_ms
            if start_ms < duration_ms:
                starts_ms.append(start_ms)

        if not starts_ms:
            messagebox.showerror("Timestamps", "No valid timestamps were generated.")
            return

        lines = [ms_to_hhmmss(ms) for ms in starts_ms]
        loaded = self._replace_timestamps_with_confirm(lines, "Episode length generator")

        if loaded:
            dropped = ep_count - len(starts_ms)
            msg = f"Generated {len(starts_ms)} timestamp(s) using episode length {ep_len_txt}."
            if dropped > 0:
                msg += f"\n\n{dropped} timestamp(s) were skipped because they were past the end of the video."

            messagebox.showinfo("Generated timestamps", msg)

    def load_every_nth_chapter_into_timestamps(self):
        in_path, duration_ms = self._get_valid_input_duration()
        if not in_path or not duration_ms:
            return

        starts_ms = ffprobe_chapter_starts_ms(in_path)
        if not starts_ms:
            messagebox.showinfo("Chapters", "No chapters found in this file.")
            return

        try:
            every_n = int((self.chapter_every_entry.get() or "1").strip())
        except Exception:
            every_n = 1

        try:
            offset = int((self.chapter_offset_entry.get() or "0").strip())
        except Exception:
            offset = 0

        if every_n <= 0:
            messagebox.showerror("Chapter interval", "Every N chapters must be greater than 0.")
            return

        if offset < 0:
            offset = 0

        if offset >= len(starts_ms):
            messagebox.showerror(
                "Chapter offset",
                f"Offset is too high. This file only has {len(starts_ms)} chapter(s)."
            )
            return

        picked = starts_ms[offset::every_n]

        # Keep the beginning of the video visible in the timestamp list.
        if 0 not in picked:
            picked.insert(0, 0)

        picked = sorted(set(ms for ms in picked if 0 <= ms < duration_ms))
        lines = [ms_to_hhmmss(ms) for ms in picked]

        loaded = self._replace_timestamps_with_confirm(lines, "Chapter interval picker")

        if loaded:
            messagebox.showinfo(
                "Chapters loaded",
                f"Loaded {len(lines)} timestamp(s).\n\n"
                f"Source chapters found: {len(starts_ms)}\n"
                f"Using every {every_n} chapter(s)\n"
                f"Offset: {offset}"
            )

    def preview_split_plan(self):
        in_path, duration_ms = self._get_valid_input_duration()
        if not in_path or not duration_ms:
            return

        ts_lines = self._get_timestamp_lines()
        starts_ms = []

        for ln in ts_lines:
            ms = parse_hhmmss_to_ms(ln)
            if ms is not None:
                starts_ms.append(ms)

        segments = build_segments_from_starts(starts_ms, duration_ms)
        if not segments:
            messagebox.showerror("Preview", "Add or generate valid timestamps first.")
            return

        stem = Path(in_path).stem
        container = self.container_opt.get()

        show_title = (self.show_title_entry.get() or "").strip() if hasattr(self, "show_title_entry") else ""
        season_txt = (self.season_entry.get() or "1").strip() if hasattr(self, "season_entry") else "1"
        start_ep_txt = (self.start_ep_entry.get() or "1").strip() if hasattr(self, "start_ep_entry") else "1"

        try:
            season_num = int(season_txt)
        except Exception:
            season_num = 1

        try:
            start_episode_num = int(start_ep_txt)
        except Exception:
            start_episode_num = 1

        safe_title = re.sub(r'[<>:"/\\|?*]+', '', (show_title or "")).strip() or stem

        durations = [end_ms - start_ms for start_ms, end_ms in segments]
        avg_duration = sum(durations) / max(len(durations), 1)

        preview_lines = [
            "Split Preview",
            "",
            f"Input: {Path(in_path).name}",
            f"Video duration: {ms_to_hhmmss(duration_ms)}",
            f"Episodes to export: {len(segments)}",
            "",
        ]

        warnings = []

        for idx, (start_ms, end_ms) in enumerate(segments):
            ep_num = start_episode_num + idx
            out_name = f"{safe_title} - S{int(season_num):02d}E{ep_num:02d}.{container}"

            seg_len_ms = end_ms - start_ms
            start_txt = ms_to_hhmmss(start_ms)
            end_txt = ms_to_hhmmss(end_ms)
            len_txt = ms_to_hhmmss(seg_len_ms)

            preview_lines.append(
                f"{out_name}\n"
                f"  {start_txt} -> {end_txt}    Length: {len_txt}"
            )

            if len(segments) > 2 and abs(seg_len_ms - avg_duration) > 120000:
                warnings.append(
                    f"S{int(season_num):02d}E{ep_num:02d} length looks different: {len_txt}"
                )

        if warnings:
            preview_lines.append("")
            preview_lines.append("Possible issues:")
            preview_lines.extend([f"- {w}" for w in warnings])

        preview_text = "\n".join(preview_lines)

        self._log("")
        self._log(preview_text)

        # Keep messagebox from getting too massive.
        display_text = preview_text
        if len(display_text) > 3500:
            display_text = display_text[:3500] + "\n\nPreview truncated. Full preview was written to the log box."

        messagebox.showinfo("Split preview", display_text)

    # ----------------------------
    # Timestamp list ops
    # ----------------------------
    def _get_timestamp_lines(self) -> List[str]:
        text = self.ts_list.get("1.0", "end").strip()
        if not text:
            return []
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    def _set_timestamp_lines(self, lines: List[str]) -> None:
        self.ts_list.delete("1.0", "end")
        for ln in lines:
            self.ts_list.insert("end", ln + "\n")


    def add_timestamp(self):
        t = (self.ts_entry.get() or "").strip()
        if not t:
            return
        if parse_hhmmss_to_ms(t) is None:
            messagebox.showerror("Timestamp", "Use HH:MM:SS (example 00:12:34)")
            return
        lines = self._get_timestamp_lines()
        lines.append(t)
        # normalize sorted
        ms_sorted = sorted({parse_hhmmss_to_ms(x) for x in lines if parse_hhmmss_to_ms(x) is not None})
        self._set_timestamp_lines([self._ms_to_hhmmss(ms) for ms in ms_sorted])
        self.ts_entry.delete(0, "end")

    def delete_selected_timestamp(self):
        # simplest: delete the line where the cursor is
        try:
            idx = self.ts_list.index("insert")
            line_no = int(str(idx).split(".")[0])
        except Exception:
            return
        lines = self._get_timestamp_lines()
        if 1 <= line_no <= len(lines):
            lines.pop(line_no - 1)
            self._set_timestamp_lines(lines)

    def _ms_to_hhmmss(self, ms: int) -> str:
        total = int(ms // 1000)
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}"

    # ----------------------------
    # Pickers
    # ----------------------------
    def pick_input(self):
        filetypes = [("Video files", "*.mp4 *.mkv *.avi *.mov *.m4v *.wmv"), ("All files", "*.*")]
        path = filedialog.askopenfilename(title="Select episode file", filetypes=filetypes)
        if not path:
            return
        self.in_entry.delete(0, "end")
        self.in_entry.insert(0, path)

    def pick_output(self):
        path = filedialog.askdirectory(title="Select output folder")
        if not path:
            return
        self.out_entry.delete(0, "end")
        self.out_entry.insert(0, path)

    def pick_left_eye(self):
        filetypes = [("Video files", "*.mp4 *.mkv *.avi *.mov *.m4v *.wmv"), ("All files", "*.*")]
        path = filedialog.askopenfilename(title="Select left eye video", filetypes=filetypes)
        if not path:
            return
        self.eye_left_entry.delete(0, "end")
        self.eye_left_entry.insert(0, path)

    def pick_right_eye(self):
        filetypes = [("Video files", "*.mp4 *.mkv *.avi *.mov *.m4v *.wmv"), ("All files", "*.*")]
        path = filedialog.askopenfilename(title="Select right eye video", filetypes=filetypes)
        if not path:
            return
        self.eye_right_entry.delete(0, "end")
        self.eye_right_entry.insert(0, path)

    # ----------------------------
    # Encode flow
    # ----------------------------
    def start_encode(self):
        if not have_ffmpeg():
            messagebox.showerror("ffmpeg missing", "ffmpeg/ffprobe not found in PATH.\nInstall ffmpeg and restart.")
            return

        in_path = (self.in_entry.get() or "").strip()
        out_dir = (self.out_entry.get() or "").strip()

        if not in_path or not os.path.exists(in_path):
            messagebox.showerror("Input", "Pick a valid input file.")
            return
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Output", "Pick a valid output folder.")
            return

        try:
            crf = int((self.crf_entry.get() or "20").strip())
        except Exception:
            messagebox.showerror("CRF", "CRF must be a number, like 18, 20, 23.")
            return

        container = self.container_opt.get()
        vcodec = self.vcodec_opt.get()
        preset = self.preset_opt.get()
        acodec = self.acodec_opt.get()
        abitrate = (self.abitrate_entry.get() or "192k").strip()

        fast_split = bool(self.fast_split_var.get()) if hasattr(self, "fast_split_var") else False
        include_subs = bool(self.include_subs_var.get()) if hasattr(self, "include_subs_var") else False

        # If fast split is enabled, force copy for video and audio
        if fast_split:
            vcodec = "copy"
            acodec = "copy"

        duration_ms = ffprobe_duration_ms(in_path)
        if not duration_ms:
            messagebox.showerror("ffprobe", "Could not read duration from input.")
            return

        # episode starts from timestamps
        ts_lines = self._get_timestamp_lines()
        starts_ms = []
        for ln in ts_lines:
            ms = parse_hhmmss_to_ms(ln)
            if ms is not None:
                starts_ms.append(ms)

        segments = build_segments_from_starts(starts_ms, duration_ms)
        if not segments:
            messagebox.showerror("Timestamps", "Add at least 1 valid start timestamp (HH:MM:SS).")
            return

        # build output naming base
        stem = Path(in_path).stem

        # ffmpeg base args (we’ll add -ss/-t per segment in the worker)
        ffmpeg_path, ffprobe_path = get_ffmpeg_tools()
        base_cmd = [ffmpeg_path, "-y", "-i", in_path]

        # Map video + all audio + optional subtitles
        base_cmd += ["-map", "0:v:0", "-map", "0:a?"]
        if include_subs:
            base_cmd += ["-map", "0:s?"]

        # --- video codec handling ---
        if vcodec == "copy":
            # copy video stream (fast, no re-encode)
            base_cmd += ["-c:v", "copy"]

        elif vcodec in ("h264_nvenc", "hevc_nvenc"):
            # NVENC does NOT use CRF. Use CQ instead.
            # We'll reuse your CRF box as CQ for NVENC.
            cq = int(crf)

            # map your x264 preset names to NVENC presets p1..p7
            preset_map = {
                "ultrafast": "p1",
                "superfast": "p2",
                "veryfast": "p3",
                "faster": "p4",
                "fast": "p4",
                "medium": "p5",
                "slow": "p6",
                "slower": "p7",
                "veryslow": "p7",
            }
            nv_preset = preset_map.get(str(preset).lower(), "p5")

            base_cmd += [
                "-c:v", vcodec,
                "-preset", nv_preset,

                # quality control (CQ)
                "-rc", "vbr",
                "-cq", str(cq),
                "-b:v", "0",

                # decent default quality improvements
                "-spatial_aq", "1",
                "-aq-strength", "8",
            ]

        else:
            # CPU encoders use CRF
            base_cmd += ["-c:v", vcodec, "-preset", preset, "-crf", str(crf)]

        # --- audio ---
        base_cmd += ["-c:a", acodec]

        if acodec != "copy":
            base_cmd += ["-b:a", abitrate]

        # --- subtitles ---
        if include_subs:
            if container == "mkv":
                # MKV supports PGS, so we can stream-copy subtitle tracks
                base_cmd += ["-c:s", "copy"]
            else:
                # MP4 does NOT support PGS. mov_text only works for text subs.
                # If the source has PGS (Blu-ray style), ffmpeg will drop them here.
                base_cmd += ["-c:s", "mov_text"]

        self._stop_flag.clear()
        self._set_ui_running(True)
        self._log_clear()
        self.progress.set(0)
        self.status_lbl.configure(text="Starting split encode...")

        self._log(f"Input:  {in_path}")
        self._log(f"Output: {out_dir}")
        self._log(f"Segments: {len(segments)} episode(s)")
        self._log("Starting split encode...")

        # --- show naming fields ---
        show_title = (self.show_title_entry.get() or "").strip() if hasattr(self, "show_title_entry") else ""
        season_txt = (self.season_entry.get() or "1").strip() if hasattr(self, "season_entry") else "1"
        start_ep_txt = (self.start_ep_entry.get() or "1").strip() if hasattr(self, "start_ep_entry") else "1"

        try:
            season_num = int(season_txt)
        except Exception:
            season_num = 1

        try:
            start_episode_num = int(start_ep_txt)
        except Exception:
            start_episode_num = 1


        self._worker_thread = threading.Thread(
            target=self._run_split_worker,
            args=(base_cmd, in_path, out_dir, stem, container, segments, duration_ms, start_episode_num, show_title, season_num),
            daemon=True
        )
        self._worker_thread.start()

    def start_single_clip_export(self):
        if not have_ffmpeg():
            messagebox.showerror(
                "ffmpeg missing",
                "ffmpeg/ffprobe not found.\n\nPlace ffmpeg.exe and ffprobe.exe beside VisionSplit.exe or add FFmpeg to PATH."
            )
            return

        in_path = (self.in_entry.get() or "").strip()
        out_dir = (self.out_entry.get() or "").strip()

        if not in_path or not os.path.exists(in_path):
            messagebox.showerror("Input", "Pick a valid input file.")
            return

        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Output", "Pick a valid output folder.")
            return

        duration_ms = ffprobe_duration_ms(in_path)
        if not duration_ms:
            messagebox.showerror("ffprobe", "Could not read duration from input.")
            return

        start_txt = (self.clip_start_entry.get() or "").strip()
        end_txt = (self.clip_end_entry.get() or "").strip()

        start_ms = parse_hhmmss_to_ms(start_txt)
        end_ms = parse_hhmmss_to_ms(end_txt)

        if start_ms is None:
            messagebox.showerror("Clip start", "Clip start must be HH:MM:SS.\n\nExample: 00:12:30")
            return

        if end_ms is None:
            messagebox.showerror("Clip end", "Clip end must be HH:MM:SS.\n\nExample: 00:14:45")
            return

        if start_ms < 0 or start_ms >= duration_ms:
            messagebox.showerror("Clip start", "Clip start is outside the video duration.")
            return

        if end_ms <= start_ms:
            messagebox.showerror("Clip range", "Clip end must be after clip start.")
            return

        if end_ms > duration_ms:
            ok = messagebox.askyesno(
                "Clip end past video",
                "Clip end is past the end of the video.\n\nUse the video end instead?"
            )
            if not ok:
                return
            end_ms = duration_ms

        clip_len_ms = end_ms - start_ms

        container = self.container_opt.get()
        vcodec = self.vcodec_opt.get()
        preset = self.preset_opt.get()
        acodec = self.acodec_opt.get()
        abitrate = (self.abitrate_entry.get() or "192k").strip()

        try:
            crf = int((self.crf_entry.get() or "20").strip())
        except Exception:
            messagebox.showerror("CRF", "CRF must be a number, like 18, 20, 23.")
            return

        fast_split = bool(self.fast_split_var.get()) if hasattr(self, "fast_split_var") else False
        include_subs = bool(self.include_subs_var.get()) if hasattr(self, "include_subs_var") else False

        if fast_split:
            vcodec = "copy"
            acodec = "copy"

        ffmpeg_path, ffprobe_path = get_ffmpeg_tools()
        if not ffmpeg_path:
            messagebox.showerror("ffmpeg missing", "ffmpeg was not found.")
            return

        ss = ms_to_hhmmss(start_ms)
        t = ms_to_hhmmss(clip_len_ms)

        raw_name = (self.clip_name_entry.get() or "").strip()
        if not raw_name:
            raw_name = f"{Path(in_path).stem}_clip_{ss.replace(':', '-')}_to_{ms_to_hhmmss(end_ms).replace(':', '-')}"

        safe_name = re.sub(r'[<>:"/\\|?*]+', "", raw_name).strip()
        if not safe_name:
            safe_name = "exported_clip"

        safe_name = Path(safe_name).stem
        out_path = os.path.join(out_dir, f"{safe_name}.{container}")

        map_args = ["-map", "0:v:0", "-map", "0:a?"]
        if include_subs:
            map_args += ["-map", "0:s?"]

        codec_args = []

        if vcodec == "copy":
            codec_args += ["-c:v", "copy"]

        elif vcodec in ("h264_nvenc", "hevc_nvenc"):
            preset_map = {
                "ultrafast": "p1",
                "superfast": "p2",
                "veryfast": "p3",
                "faster": "p4",
                "fast": "p4",
                "medium": "p5",
                "slow": "p6",
                "slower": "p7",
                "veryslow": "p7",
            }
            nv_preset = preset_map.get(str(preset).lower(), "p5")

            codec_args += [
                "-c:v", vcodec,
                "-preset", nv_preset,
                "-rc", "vbr",
                "-cq", str(crf),
                "-b:v", "0",
                "-spatial_aq", "1",
                "-aq-strength", "8",
            ]

        else:
            codec_args += ["-c:v", vcodec, "-preset", preset, "-crf", str(crf)]

        codec_args += ["-c:a", acodec]

        if acodec != "copy":
            codec_args += ["-b:a", abitrate]

        if include_subs:
            if container == "mkv":
                codec_args += ["-c:s", "copy"]
            else:
                codec_args += ["-c:s", "mov_text"]

        if fast_split:
            cmd = [
                ffmpeg_path,
                "-y",
                "-ss", ss,
                "-i", in_path,
                "-t", t,
            ] + map_args + codec_args + [
                "-avoid_negative_ts", "make_zero",
                "-progress", "pipe:1",
                "-nostats",
                out_path
            ]
        else:
            cmd = [
                ffmpeg_path,
                "-y",
                "-i", in_path,
                "-ss", ss,
                "-t", t,
            ] + map_args + codec_args + [
                "-progress", "pipe:1",
                "-nostats",
                out_path
            ]

        self._stop_flag.clear()
        self._set_ui_running(True)
        self._log_clear()
        self.progress.set(0)
        self.status_lbl.configure(text="Starting single clip export...")

        self._log(f"Input:  {in_path}")
        self._log(f"Output: {out_path}")
        self._log(f"Clip:   {ss} -> {ms_to_hhmmss(end_ms)}")
        self._log("Starting single clip export...")

        self._worker_thread = threading.Thread(
            target=self._run_single_clip_worker,
            args=(cmd, out_path, clip_len_ms),
            daemon=True
        )
        self._worker_thread.start()

    def _run_single_clip_worker(self, cmd: List[str], out_path: str, clip_len_ms: int):
        def normalize_ffmpeg_progress_time(raw_value: str, expected_len_ms: int) -> int:
            try:
                value = int(str(raw_value).strip())
            except Exception:
                return 0

            # Some FFmpeg builds report this as microseconds.
            if expected_len_ms > 0 and value > expected_len_ms * 20:
                value = value // 1000

            return max(0, value)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
        except Exception as e:
            self._ui_queue.put(("done", False, f"Failed to launch ffmpeg: {e}", None))
            return

        last_pct = 0.0

        try:
            while True:
                if self._stop_flag.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass

                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    if proc.poll() is not None:
                        break
                    continue

                line = line.strip()

                if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                    raw = line.split("=", 1)[1].strip()
                    out_ms = normalize_ffmpeg_progress_time(raw, clip_len_ms)

                    pct = max(0.0, min(1.0, out_ms / max(clip_len_ms, 1)))

                    if abs(pct - last_pct) >= 0.002:
                        last_pct = pct
                        self._ui_queue.put(("progress", pct, f"Exporting clip... {pct * 100:.1f}%"))

                elif line.startswith("progress=end"):
                    self._ui_queue.put(("progress", 1.0, "Clip export complete."))

                elif line and not line.startswith(("frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time=", "dup_frames=", "drop_frames=", "speed=", "progress=")):
                    self._ui_queue.put(("log", line))

            rc = proc.wait()

            if rc != 0:
                if self._stop_flag.is_set():
                    self._ui_queue.put(("done", False, "Stopped by user.", None))
                else:
                    self._ui_queue.put(("done", False, f"ffmpeg clip export failed (code {rc}).", None))
                return

            self._ui_queue.put(("progress", 1.0, "Clip export complete."))
            self._ui_queue.put(("done", True, f"Done. Exported clip:\n{out_path}", None))

        except Exception as e:
            self._ui_queue.put(("done", False, f"Error during clip export: {e}", None))

    def stop_encode(self):
        self._stop_flag.set()
        self._log("Stop requested...")

    def _safe_output_stem(self, raw_name: str, fallback: str) -> str:
        safe_name = re.sub(r'[<>:"/\\|?*]+', "", raw_name or "").strip()
        if not safe_name:
            safe_name = fallback
        safe_name = Path(safe_name).stem
        return safe_name or "stereo_output"

    def _stereo_video_codec_args(self, vcodec: str, preset: str, crf: int) -> List[str]:
        """
        Stereo split/merge uses filters, so video copy is not possible.
        If copy is selected, fall back to libx264.
        """
        if vcodec == "copy":
            vcodec = "libx264"

        if vcodec in ("h264_nvenc", "hevc_nvenc"):
            preset_map = {
                "ultrafast": "p1",
                "superfast": "p2",
                "veryfast": "p3",
                "faster": "p4",
                "fast": "p4",
                "medium": "p5",
                "slow": "p6",
                "slower": "p7",
                "veryslow": "p7",
                "p1": "p1",
                "p2": "p2",
                "p3": "p3",
                "p4": "p4",
                "p5": "p5",
                "p6": "p6",
                "p7": "p7",
            }
            nv_preset = preset_map.get(str(preset).lower(), "p5")

            return [
                "-c:v", vcodec,
                "-preset", nv_preset,
                "-rc", "vbr",
                "-cq", str(crf),
                "-b:v", "0",
                "-spatial_aq", "1",
                "-aq-strength", "8",
            ]

        cpu_presets = {
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow"
        }
        cpu_preset = preset if preset in cpu_presets else "medium"

        if vcodec not in ("libx264", "libx265"):
            vcodec = "libx264"

        return [
            "-c:v", vcodec,
            "-preset", cpu_preset,
            "-crf", str(crf),
        ]

    def _stereo_audio_codec_args(self, acodec: str, abitrate: str) -> List[str]:
        if acodec == "copy":
            return ["-c:a", "copy"]
        return ["-c:a", acodec, "-b:a", abitrate]

    def start_split_full_sbs(self):
        if not have_ffmpeg():
            messagebox.showerror(
                "ffmpeg missing",
                "ffmpeg/ffprobe not found.\n\nPlace ffmpeg.exe and ffprobe.exe beside VisionSplit.exe or add FFmpeg to PATH."
            )
            return

        in_path = (self.in_entry.get() or "").strip()
        out_dir = (self.out_entry.get() or "").strip()

        if not in_path or not os.path.exists(in_path):
            messagebox.showerror("Input", "Pick a valid Full SBS input file first.")
            return

        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Output", "Pick a valid output folder.")
            return

        duration_ms = ffprobe_duration_ms(in_path)
        if not duration_ms:
            messagebox.showerror("ffprobe", "Could not read duration from input.")
            return

        try:
            crf = int((self.crf_entry.get() or "20").strip())
        except Exception:
            messagebox.showerror("CRF", "CRF must be a number, like 18, 20, 23.")
            return

        container = self.container_opt.get()
        vcodec = self.vcodec_opt.get()
        preset = self.preset_opt.get()
        acodec = self.acodec_opt.get()
        abitrate = (self.abitrate_entry.get() or "192k").strip()

        ffmpeg_path, ffprobe_path = get_ffmpeg_tools()
        if not ffmpeg_path:
            messagebox.showerror("ffmpeg missing", "ffmpeg was not found.")
            return

        raw_name = (self.stereo_name_entry.get() or "").strip()
        safe_name = self._safe_output_stem(raw_name, Path(in_path).stem)

        left_path = os.path.join(out_dir, f"{safe_name}_LeftEye.{container}")
        right_path = os.path.join(out_dir, f"{safe_name}_RightEye.{container}")

        video_args = self._stereo_video_codec_args(vcodec, preset, crf)
        audio_args = self._stereo_audio_codec_args(acodec, abitrate)

        filter_complex = (
            "[0:v]crop=iw/2:ih:0:0,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2[leftv];"
            "[0:v]crop=iw/2:ih:iw/2:0,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2[rightv]"
        )

        cmd = [
            ffmpeg_path,
            "-y",
            "-i", in_path,
            "-filter_complex", filter_complex,
            "-progress", "pipe:1",
            "-nostats",

            "-map", "[leftv]",
            "-map", "0:a?",
        ] + video_args + audio_args + [
            left_path,

            "-map", "[rightv]",
            "-map", "0:a?",
        ] + video_args + audio_args + [
            right_path
        ]

        self._stop_flag.clear()
        self._set_ui_running(True)
        self._log_clear()
        self.progress.set(0)
        self.status_lbl.configure(text="Splitting Full SBS into left and right eyes...")

        self._log(f"Input Full SBS: {in_path}")
        self._log(f"Left eye output:  {left_path}")
        self._log(f"Right eye output: {right_path}")

        if vcodec == "copy":
            self._log("Note: Stereo split uses crop filters, so video copy is not possible. Falling back to libx264.")

        self._worker_thread = threading.Thread(
            target=self._run_stereo_worker,
            args=(cmd, duration_ms, f"Done. Exported stereo eyes:\n{left_path}\n{right_path}"),
            daemon=True
        )
        self._worker_thread.start()

    def start_merge_eyes_to_full_sbs(self):
        if not have_ffmpeg():
            messagebox.showerror(
                "ffmpeg missing",
                "ffmpeg/ffprobe not found.\n\nPlace ffmpeg.exe and ffprobe.exe beside VisionSplit.exe or add FFmpeg to PATH."
            )
            return

        left_path = (self.eye_left_entry.get() or "").strip()
        right_path = (self.eye_right_entry.get() or "").strip()
        out_dir = (self.out_entry.get() or "").strip()

        if not left_path or not os.path.exists(left_path):
            messagebox.showerror("Left eye", "Pick a valid left eye video.")
            return

        if not right_path or not os.path.exists(right_path):
            messagebox.showerror("Right eye", "Pick a valid right eye video.")
            return

        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Output", "Pick a valid output folder.")
            return

        left_duration = ffprobe_duration_ms(left_path)
        right_duration = ffprobe_duration_ms(right_path)

        if not left_duration or not right_duration:
            messagebox.showerror("ffprobe", "Could not read duration from one of the eye videos.")
            return

        duration_ms = min(left_duration, right_duration)

        try:
            crf = int((self.crf_entry.get() or "20").strip())
        except Exception:
            messagebox.showerror("CRF", "CRF must be a number, like 18, 20, 23.")
            return

        container = self.container_opt.get()
        vcodec = self.vcodec_opt.get()
        preset = self.preset_opt.get()
        acodec = self.acodec_opt.get()
        abitrate = (self.abitrate_entry.get() or "192k").strip()

        ffmpeg_path, ffprobe_path = get_ffmpeg_tools()
        if not ffmpeg_path:
            messagebox.showerror("ffmpeg missing", "ffmpeg was not found.")
            return

        raw_name = (self.stereo_name_entry.get() or "").strip()
        fallback_name = f"{Path(left_path).stem}_Full_SBS"
        safe_name = self._safe_output_stem(raw_name, fallback_name)

        out_path = os.path.join(out_dir, f"{safe_name}.{container}")

        video_args = self._stereo_video_codec_args(vcodec, preset, crf)
        audio_args = self._stereo_audio_codec_args(acodec, abitrate)

        filter_complex = (
            "[0:v]setpts=PTS-STARTPTS,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2[leftv];"
            "[1:v]setpts=PTS-STARTPTS,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2[rightv];"
            "[leftv][rightv]hstack=inputs=2[v]"
        )

        cmd = [
            ffmpeg_path,
            "-y",
            "-i", left_path,
            "-i", right_path,
            "-filter_complex", filter_complex,
            "-progress", "pipe:1",
            "-nostats",
            "-map", "[v]",
            "-map", "0:a?",
        ] + video_args + audio_args + [
            "-shortest",
            out_path
        ]

        self._stop_flag.clear()
        self._set_ui_running(True)
        self._log_clear()
        self.progress.set(0)
        self.status_lbl.configure(text="Merging left and right eyes into Full SBS...")

        self._log(f"Left eye:  {left_path}")
        self._log(f"Right eye: {right_path}")
        self._log(f"Output Full SBS: {out_path}")

        if vcodec == "copy":
            self._log("Note: Stereo merge uses hstack filters, so video copy is not possible. Falling back to libx264.")

        self._worker_thread = threading.Thread(
            target=self._run_stereo_worker,
            args=(cmd, duration_ms, f"Done. Exported Full SBS:\n{out_path}"),
            daemon=True
        )
        self._worker_thread.start()

    def _run_stereo_worker(self, cmd: List[str], duration_ms: int, done_message: str):
        def normalize_ffmpeg_progress_time(raw_value: str, expected_len_ms: int) -> int:
            try:
                value = int(str(raw_value).strip())
            except Exception:
                return 0

            if expected_len_ms > 0 and value > expected_len_ms * 20:
                value = value // 1000

            return max(0, value)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
        except Exception as e:
            self._ui_queue.put(("done", False, f"Failed to launch ffmpeg: {e}", None))
            return

        last_pct = 0.0

        try:
            while True:
                if self._stop_flag.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass

                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    if proc.poll() is not None:
                        break
                    continue

                line = line.strip()

                if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                    raw = line.split("=", 1)[1].strip()
                    out_ms = normalize_ffmpeg_progress_time(raw, duration_ms)

                    pct = max(0.0, min(1.0, out_ms / max(duration_ms, 1)))

                    if abs(pct - last_pct) >= 0.002:
                        last_pct = pct
                        self._ui_queue.put(("progress", pct, f"Stereo operation... {pct * 100:.1f}%"))

                elif line.startswith("progress=end"):
                    self._ui_queue.put(("progress", 1.0, "Stereo operation complete."))

                elif line and not line.startswith(("frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time=", "dup_frames=", "drop_frames=", "speed=", "progress=")):
                    self._ui_queue.put(("log", line))

            rc = proc.wait()

            if rc != 0:
                if self._stop_flag.is_set():
                    self._ui_queue.put(("done", False, "Stopped by user.", None))
                else:
                    self._ui_queue.put(("done", False, f"ffmpeg stereo operation failed (code {rc}).", None))
                return

            self._ui_queue.put(("progress", 1.0, "Stereo operation complete."))
            self._ui_queue.put(("done", True, done_message, None))

        except Exception as e:
            self._ui_queue.put(("done", False, f"Error during stereo operation: {e}", None))

    def _run_split_worker(
        self,
        base_cmd: list[str],
        in_path: str,
        out_dir: str,
        stem: str,
        container: str,
        segments: list[tuple[int, int]],
        duration_ms: int,
        start_ep_num: int = 1,
        show_title: str = "",
        season_num: int = 1
    ):
        """
        Encode each (start_ms, end_ms) as its own output file.
        Progress bar shows overall progress across all segments.
        Works for both fast stream-copy splitting and re-encode splitting.
        """
        total_work_ms = sum((e - s) for (s, e) in segments)
        done_work_ms = 0

        ffmpeg_path, ffprobe_path = get_ffmpeg_tools()
        if not ffmpeg_path:
            self._ui_queue.put(("done", False, "ffmpeg not found.", None))
            return

        def normalize_ffmpeg_progress_time(raw_value: str, expected_len_ms: int) -> int:
            """
            FFmpeg progress can report out_time_ms as microseconds on some builds.
            This normalizes it back to milliseconds when needed.
            """
            try:
                value = int(str(raw_value).strip())
            except Exception:
                return 0

            if expected_len_ms > 0 and value > expected_len_ms * 20:
                value = value // 1000

            return max(0, value)

        def push_progress(done_ms: int, msg: str):
            pct = 0.0 if total_work_ms <= 0 else max(0.0, min(1.0, done_ms / total_work_ms))
            self._ui_queue.put(("progress", pct, msg))

        push_progress(0, "Starting split encode...")

        for idx, (start_ms, end_ms) in enumerate(segments, start=0):
            if self._stop_flag.is_set():
                self._ui_queue.put(("done", False, "Stopped by user.", None))
                return

            ep_num = start_ep_num + idx
            safe_title = re.sub(r'[<>:"/\\|?*]+', '', (show_title or "")).strip() or stem
            out_name = f"{safe_title} - S{int(season_num):02d}E{ep_num:02d}.{container}"
            out_path = os.path.join(out_dir, out_name)

            seg_dur_ms = max(1, end_ms - start_ms)
            ss = ms_to_hhmmss(start_ms)
            to = ms_to_hhmmss(end_ms)
            t = ms_to_hhmmss(seg_dur_ms)

            fast_split = ("-c:v" in base_cmd) and (base_cmd[base_cmd.index("-c:v") + 1] == "copy")

            if fast_split:
                # Fast keyframe-aligned cut: -ss before -i
                tail = base_cmd.copy()
                i_pos = tail.index("-i")
                tail = tail[i_pos + 2:]  # everything after input path

                cmd = [ffmpeg_path, "-y", "-ss", ss, "-i", in_path] + tail + ["-t", t]
            else:
                # Accurate cut: -ss after -i
                cmd = base_cmd.copy()
                try:
                    i_pos = cmd.index("-i")
                    insert_at = i_pos + 2
                    cmd[insert_at:insert_at] = ["-ss", ss, "-t", t]
                except ValueError:
                    cmd = [ffmpeg_path, "-y", "-i", in_path, "-ss", ss, "-t", t] + cmd[3:]

            # Progress output must be before the output path.
            cmd += ["-progress", "pipe:1", "-nostats", out_path]

            self._ui_queue.put(("log", f"\nEpisode {ep_num:02d}: {ss} -> {to}"))
            self._ui_queue.put(("log", f"Writing: {out_path}"))

            mode_text = "Splitting" if fast_split else "Encoding"
            push_progress(done_work_ms, f"{mode_text} episode {ep_num:02d}... 0.0%")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
            except Exception as e:
                self._ui_queue.put(("done", False, f"Failed to launch ffmpeg: {e}", None))
                return

            seg_last_pct = 0.0

            while True:
                if self._stop_flag.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass

                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    if proc.poll() is not None:
                        break
                    continue

                line = line.strip()

                if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                    try:
                        raw = line.split("=", 1)[1].strip()
                        out_ms = normalize_ffmpeg_progress_time(raw, seg_dur_ms)

                        seg_pct = max(0.0, min(1.0, out_ms / seg_dur_ms))
                        overall_done = done_work_ms + int(seg_pct * seg_dur_ms)

                        if abs(seg_pct - seg_last_pct) >= 0.002:
                            seg_last_pct = seg_pct
                            overall_pct = 0.0 if total_work_ms <= 0 else max(0.0, min(100.0, (overall_done / total_work_ms) * 100.0))
                            push_progress(
                                overall_done,
                                f"{mode_text} episode {ep_num:02d}... {seg_pct * 100:.1f}% | Overall {overall_pct:.1f}%"
                            )
                    except Exception:
                        pass

                elif line.startswith("progress=end"):
                    push_progress(
                        done_work_ms + seg_dur_ms,
                        f"Finished episode {ep_num:02d}"
                    )

                elif line and not line.startswith(("frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time=", "dup_frames=", "drop_frames=", "speed=", "progress=")):
                    # Keep important FFmpeg warnings/errors without flooding the log.
                    self._ui_queue.put(("log", line))

            rc = proc.wait()
            if rc != 0:
                if self._stop_flag.is_set():
                    self._ui_queue.put(("done", False, "Stopped by user.", None))
                else:
                    self._ui_queue.put(("done", False, f"ffmpeg failed on episode {ep_num:02d} (code {rc}).", None))
                return

            done_work_ms += seg_dur_ms
            push_progress(done_work_ms, f"Finished episode {ep_num:02d}")

        self._ui_queue.put(("progress", 1.0, "Split encode complete."))
        self._ui_queue.put(("done", True, f"Done. Exported {len(segments)} episodes.", None))


    def _run_ffmpeg_worker(self, cmd: List[str], meta_path: str, duration_ms: int):
        start_time = time.time()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
        except Exception as e:
            self._ui_queue.put(("done", False, f"Failed to launch ffmpeg: {e}", meta_path))
            return

        last_progress = 0.0

        try:
            while True:
                if self._stop_flag.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass

                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    if proc.poll() is not None:
                        break
                    continue

                line = line.strip()

                # ffmpeg progress lines look like: out_time_ms=1234567
                if line.startswith("out_time_ms="):
                    try:
                        out_ms = int(line.split("=", 1)[1].strip())
                        pct = max(0.0, min(1.0, out_ms / max(duration_ms, 1)))
                        # reduce spam
                        if abs(pct - last_progress) >= 0.002:
                            last_progress = pct
                            self._ui_queue.put(("progress", pct, f"Encoding... {pct*100:.1f}%"))
                    except Exception:
                        pass

                if line.startswith("progress="):
                    # end
                    pass

                # Optional: show a few log lines
                if line.startswith("frame=") or line.startswith("speed=") or line.startswith("bitrate="):
                    self._ui_queue.put(("log", line))

            rc = proc.wait()
            ok = (rc == 0) and (not self._stop_flag.is_set())

            elapsed = time.time() - start_time
            if ok:
                self._ui_queue.put(("done", True, f"Done. Time: {elapsed:.1f}s", meta_path))
            else:
                if self._stop_flag.is_set():
                    self._ui_queue.put(("done", False, "Stopped by user.", meta_path))
                else:
                    self._ui_queue.put(("done", False, f"ffmpeg failed (code {rc}).", meta_path))

        except Exception as e:
            self._ui_queue.put(("done", False, f"Error: {e}", meta_path))

    def _drain_ui_queue(self):
        try:
            while True:
                item = self._ui_queue.get_nowait()
                kind = item[0]

                if kind == "progress":
                    pct, msg = item[1], item[2]
                    self.progress.set(float(pct))
                    self.status_lbl.configure(text=msg)

                elif kind == "log":
                    self._log(item[1])

                elif kind == "done":
                    ok, msg, meta_path = item[1], item[2], item[3]
                    self.progress.set(1.0 if ok else 0.0)
                    self.status_lbl.configure(text=msg)
                    self._log(msg)
                    self._set_ui_running(False)

                    # cleanup temp metadata
                    try:
                        if meta_path and os.path.exists(meta_path):
                            os.remove(meta_path)
                    except Exception:
                        pass

        except queue.Empty:
            pass

        self.after(100, self._drain_ui_queue)

    def _set_ui_running(self, running: bool):
        state = "disabled" if running else "normal"

        if hasattr(self, "start_btn"):
            self.start_btn.configure(state=state)

        if hasattr(self, "stitch_btn"):
            self.stitch_btn.configure(state=state)

        if hasattr(self, "single_clip_btn"):
            self.single_clip_btn.configure(state=state)

        if hasattr(self, "sbs_split_btn"):
            self.sbs_split_btn.configure(state=state)

        if hasattr(self, "sbs_merge_btn"):
            self.sbs_merge_btn.configure(state=state)

        self.stop_btn.configure(state="normal" if running else "disabled")
    # ----------------------------
    # Log
    # ----------------------------
    def _log_clear(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _refresh_stitch_list(self):
        self.stitch_list.delete("1.0", "end")
        for i, path in enumerate(self.stitch_clips, start=1):
            self.stitch_list.insert("end", f"{i:02d}. {path}\n")

    def _get_selected_stitch_index(self) -> Optional[int]:
        try:
            idx = self.stitch_list.index("insert")
            line_no = int(str(idx).split(".")[0])
        except Exception:
            return None
        if 1 <= line_no <= len(self.stitch_clips):
            return line_no - 1
        return None

    def add_stitch_clips(self):
        filetypes = [("Video files", "*.mp4 *.mkv *.avi *.mov *.m4v *.wmv"), ("All files", "*.*")]
        paths = filedialog.askopenfilenames(title="Select clips to stitch", filetypes=filetypes)
        if not paths:
            return
        self.stitch_clips.extend(paths)
        self._refresh_stitch_list()

    def remove_selected_stitch_clip(self):
        idx = self._get_selected_stitch_index()
        if idx is None:
            return
        self.stitch_clips.pop(idx)
        self._refresh_stitch_list()

    def move_stitch_clip_up(self):
        idx = self._get_selected_stitch_index()
        if idx is None or idx <= 0:
            return
        self.stitch_clips[idx - 1], self.stitch_clips[idx] = self.stitch_clips[idx], self.stitch_clips[idx - 1]
        self._refresh_stitch_list()

    def move_stitch_clip_down(self):
        idx = self._get_selected_stitch_index()
        if idx is None or idx >= len(self.stitch_clips) - 1:
            return
        self.stitch_clips[idx + 1], self.stitch_clips[idx] = self.stitch_clips[idx], self.stitch_clips[idx + 1]
        self._refresh_stitch_list()

    def clear_stitch_clips(self):
        if not self.stitch_clips:
            return
        ok = messagebox.askyesno("Clear clips?", f"Clear all {len(self.stitch_clips)} clips from the stitch list?")
        if not ok:
            return
        self.stitch_clips = []
        self._refresh_stitch_list()
        
    def start_stitch(self):
        if not have_ffmpeg():
            messagebox.showerror("ffmpeg missing", "ffmpeg/ffprobe not found in PATH.\nInstall ffmpeg and restart.")
            return

        out_dir = (self.out_entry.get() or "").strip()
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Output", "Pick a valid output folder.")
            return

        if len(self.stitch_clips) < 2:
            messagebox.showerror("Clips", "Add at least 2 clips to stitch.")
            return

        missing = [p for p in self.stitch_clips if not os.path.exists(p)]
        if missing:
            messagebox.showerror("Missing files", f"Some clips no longer exist:\n\n{missing[0]}")
            return

        container = self.container_opt.get()
        vcodec = self.vcodec_opt.get()
        preset = self.preset_opt.get()
        acodec = self.acodec_opt.get()
        abitrate = (self.abitrate_entry.get() or "192k").strip()

        try:
            crf = int((self.crf_entry.get() or "20").strip())
        except Exception:
            messagebox.showerror("CRF", "CRF must be a number, like 18, 20, 23.")
            return

        out_path = os.path.join(out_dir, f"stitched_output.{container}")

        self._stop_flag.clear()
        self._set_ui_running(True)
        self._log_clear()
        self.progress.set(0)
        self.status_lbl.configure(text="Preparing stitch job...")

        self._worker_thread = threading.Thread(
            target=self._run_stitch_worker,
            args=(self.stitch_clips[:], out_path, container, vcodec, preset, crf, acodec, abitrate),
            daemon=True
        )
        self._worker_thread.start()
        
        
    def _run_stitch_worker(
        self,
        clips: List[str],
        out_path: str,
        container: str,
        vcodec: str,
        preset: str,
        crf: int,
        acodec: str,
        abitrate: str
    ):
        ffmpeg_path, ffprobe_path = get_ffmpeg_tools()
        if not ffmpeg_path:
            self._ui_queue.put(("done", False, "ffmpeg not found.", None))
            return

        def normalize_ffmpeg_progress_time(raw_value: str, expected_len_ms: int) -> int:
            """
            FFmpeg progress can report out_time_ms as microseconds on some builds.
            This normalizes it back to milliseconds when needed.
            """
            try:
                value = int(str(raw_value).strip())
            except Exception:
                return 0

            if expected_len_ms > 0 and value > expected_len_ms * 20:
                value = value // 1000

            return max(0, value)

        # Get total stitch duration so the progress bar has a real target.
        clip_durations_ms = []
        for clip in clips:
            dur = ffprobe_duration_ms(clip)
            if dur:
                clip_durations_ms.append(dur)

        total_duration_ms = sum(clip_durations_ms)

        concat_list_path = os.path.join(os.path.dirname(out_path), "concat_list.txt")

        try:
            with open(concat_list_path, "w", encoding="utf-8") as f:
                for clip in clips:
                    safe = clip.replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{safe}'\n")
        except Exception as e:
            self._ui_queue.put(("done", False, f"Failed to create concat list: {e}", None))
            return

        fast_mode = (vcodec == "copy" and acodec == "copy")

        if fast_mode:
            cmd = [
                ffmpeg_path, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                "-progress", "pipe:1",
                "-nostats",
                out_path
            ]
        else:
            cmd = [
                ffmpeg_path, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c:v", vcodec,
            ]

            if vcodec in ("h264_nvenc", "hevc_nvenc"):
                preset_map = {
                    "ultrafast": "p1",
                    "superfast": "p2",
                    "veryfast": "p3",
                    "faster": "p4",
                    "fast": "p4",
                    "medium": "p5",
                    "slow": "p6",
                    "slower": "p7",
                    "veryslow": "p7",
                }
                nv_preset = preset_map.get(str(preset).lower(), "p5")
                cmd += [
                    "-preset", nv_preset,
                    "-rc", "vbr",
                    "-cq", str(crf),
                    "-b:v", "0",
                    "-spatial_aq", "1",
                    "-aq-strength", "8",
                ]
            elif vcodec != "copy":
                cmd += ["-preset", preset, "-crf", str(crf)]
            else:
                cmd += ["-c:v", "copy"]

            if acodec == "copy":
                cmd += ["-c:a", "copy"]
            else:
                cmd += ["-c:a", acodec, "-b:a", abitrate]

            cmd += [
                "-progress", "pipe:1",
                "-nostats",
                out_path
            ]

        self._ui_queue.put(("log", f"Stitching {len(clips)} clip(s)..."))
        self._ui_queue.put(("log", f"Output: {out_path}"))

        if total_duration_ms > 0:
            self._ui_queue.put(("progress", 0.0, "Stitching clips... 0.0%"))
        else:
            self._ui_queue.put(("progress", 0.0, "Stitching clips..."))

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
        except Exception as e:
            try:
                if os.path.exists(concat_list_path):
                    os.remove(concat_list_path)
            except Exception:
                pass
            self._ui_queue.put(("done", False, f"Failed to launch ffmpeg: {e}", None))
            return

        last_pct = 0.0

        try:
            while True:
                if self._stop_flag.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass

                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    if proc.poll() is not None:
                        break
                    continue

                line = line.strip()

                if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                    if total_duration_ms > 0:
                        raw = line.split("=", 1)[1].strip()
                        out_ms = normalize_ffmpeg_progress_time(raw, total_duration_ms)
                        pct = max(0.0, min(1.0, out_ms / total_duration_ms))

                        if abs(pct - last_pct) >= 0.002:
                            last_pct = pct
                            self._ui_queue.put(("progress", pct, f"Stitching clips... {pct * 100:.1f}%"))

                elif line.startswith("progress=end"):
                    self._ui_queue.put(("progress", 1.0, "Stitch complete."))

                elif line and not line.startswith(("frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time=", "dup_frames=", "drop_frames=", "speed=", "progress=")):
                    # Keep useful warnings/errors without flooding the log.
                    self._ui_queue.put(("log", line))

            rc = proc.wait()

            try:
                if os.path.exists(concat_list_path):
                    os.remove(concat_list_path)
            except Exception:
                pass

            if rc != 0:
                if self._stop_flag.is_set():
                    self._ui_queue.put(("done", False, "Stopped by user.", None))
                else:
                    self._ui_queue.put(("done", False, f"ffmpeg stitch failed (code {rc}).", None))
                return

            self._ui_queue.put(("progress", 1.0, "Stitch complete."))
            self._ui_queue.put(("done", True, f"Done. Stitched {len(clips)} clips.", None))

        except Exception as e:
            try:
                if os.path.exists(concat_list_path):
                    os.remove(concat_list_path)
            except Exception:
                pass
            self._ui_queue.put(("done", False, f"Error during stitch: {e}", None))
            
    # ----------------------------
    # Close
    # ----------------------------
    def on_close(self):
        self.update_idletasks()

        self.settings["fast_split"] = bool(self.fast_split_var.get())
        self.settings["include_subs"] = bool(self.include_subs_var.get())

        self.settings["window_geometry"] = self.geometry()
        self.settings["appearance_mode"] = str(ctk.get_appearance_mode())

        self.settings["last_input"] = (self.in_entry.get() or "").strip()
        self.settings["last_output"] = (self.out_entry.get() or "").strip()

        self.settings["container"] = self.container_opt.get()
        self.settings["vcodec"] = self.vcodec_opt.get()
        self.settings["preset"] = self.preset_opt.get()
        try:
            self.settings["crf"] = int((self.crf_entry.get() or "20").strip())
        except Exception:
            self.settings["crf"] = 20
        self.settings["acodec"] = self.acodec_opt.get()
        self.settings["abitrate"] = (self.abitrate_entry.get() or "192k").strip()

        self.settings["show_title"] = (self.show_title_entry.get() or "").strip()
        self.settings["season"] = (self.season_entry.get() or "1").strip()
        self.settings["start_ep"] = (self.start_ep_entry.get() or "1").strip()

        if hasattr(self, "smart_ep_len_entry"):
            self.settings["smart_ep_len"] = (self.smart_ep_len_entry.get() or "00:23:40").strip()

        if hasattr(self, "smart_ep_count_entry"):
            self.settings["smart_ep_count"] = (self.smart_ep_count_entry.get() or "4").strip()

        if hasattr(self, "chapter_every_entry"):
            self.settings["chapter_every"] = (self.chapter_every_entry.get() or "5").strip()

        if hasattr(self, "chapter_offset_entry"):
            self.settings["chapter_offset"] = (self.chapter_offset_entry.get() or "0").strip()

        if hasattr(self, "clip_start_entry"):
            self.settings["clip_start"] = (self.clip_start_entry.get() or "00:00:00").strip()

        if hasattr(self, "clip_end_entry"):
            self.settings["clip_end"] = (self.clip_end_entry.get() or "00:01:00").strip()

        if hasattr(self, "clip_name_entry"):
            self.settings["clip_name"] = (self.clip_name_entry.get() or "").strip()
            
        if hasattr(self, "stereo_name_entry"):
            self.settings["stereo_name"] = (self.stereo_name_entry.get() or "").strip()

        if hasattr(self, "eye_left_entry"):
            self.settings["eye_left"] = (self.eye_left_entry.get() or "").strip()

        if hasattr(self, "eye_right_entry"):
            self.settings["eye_right"] = (self.eye_right_entry.get() or "").strip()

        save_settings(self.settings)
        self.destroy()  


if __name__ == "__main__":
    app = EpisodeEncoderApp()
    app.mainloop()
