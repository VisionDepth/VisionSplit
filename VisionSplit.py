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

SETTINGS_FILE = Path("episode_encoder_settings.json")

import shutil

def resolve_ffmpeg_tools():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)
        ffmpeg_path = base_path / "ffmpeg.exe"
        ffprobe_path = base_path / "ffprobe.exe"

        if ffmpeg_path.exists() and ffprobe_path.exists():
            return str(ffmpeg_path), str(ffprobe_path)

    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    ffmpeg_local = exe_dir / "ffmpeg.exe"
    ffprobe_local = exe_dir / "ffprobe.exe"

    if ffmpeg_local.exists() and ffprobe_local.exists():
        return str(ffmpeg_local), str(ffprobe_local)

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
    # sanitize + sort + unique + keep in-range
    starts = sorted({ms for ms in starts_ms if 0 <= ms < duration_ms})
    if not starts:
        return []
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


# ----------------------------
# UI App
# ----------------------------
class EpisodeEncoderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Episode Encoder")
        self.settings = load_settings()

        self.geometry(self.settings.get("window_geometry", "980x720"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        ctk.set_appearance_mode(self.settings.get("appearance_mode", "Dark"))
        ctk.set_default_color_theme("blue")

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._ui_queue: "queue.Queue[tuple]" = queue.Queue()

        self._build_ui()
        self.after(100, self._drain_ui_queue)

    def _build_ui(self):
        root = ctk.CTkFrame(self)
        root.pack(fill="both", expand=True, padx=14, pady=14)

        # ----------------------------
        # Top: Input + Output
        # ----------------------------
        top = ctk.CTkFrame(root)
        top.pack(fill="x", padx=10, pady=(10, 8))

        # Input file
        ctk.CTkLabel(top, text="Input file", width=90, anchor="w").grid(row=0, column=0, padx=(10, 6), pady=(10, 6), sticky="w")
        self.in_entry = ctk.CTkEntry(top, placeholder_text="Pick an episode video file...")
        self.in_entry.grid(row=0, column=1, padx=6, pady=(10, 6), sticky="ew")
        ctk.CTkButton(top, text="Browse", width=90, command=self.pick_input).grid(row=0, column=2, padx=(6, 10), pady=(10, 6))

        # Output folder
        ctk.CTkLabel(top, text="Output folder", width=90, anchor="w").grid(row=1, column=0, padx=(10, 6), pady=(6, 10), sticky="w")
        self.out_entry = ctk.CTkEntry(top, placeholder_text="Pick an output folder...")
        self.out_entry.grid(row=1, column=1, padx=6, pady=(6, 10), sticky="ew")
        ctk.CTkButton(top, text="Browse", width=90, command=self.pick_output).grid(row=1, column=2, padx=(6, 10), pady=(6, 10))

        top.grid_columnconfigure(1, weight=1)

        # ----------------------------
        # Middle: Timestamp box + Encoder settings
        # ----------------------------
        mid = ctk.CTkFrame(root)
        mid.pack(fill="both", expand=True, padx=10, pady=8)

        # Timestamp box (left)
        ts_frame = ctk.CTkFrame(mid)
        ts_frame.pack(side="left", fill="both", expand=True, padx=(10, 6), pady=10)

        ctk.CTkLabel(ts_frame, text="Timestamp box (chapters)", anchor="w").pack(fill="x", padx=10, pady=(10, 4))

        ts_row = ctk.CTkFrame(ts_frame)
        ts_row.pack(fill="x", padx=10, pady=(0, 8))
        self.ts_entry = ctk.CTkEntry(ts_row, placeholder_text="HH:MM:SS  (example 00:12:34)")
        self.ts_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(ts_row, text="+ Add", width=70, command=self.add_timestamp).pack(side="left")

        ctk.CTkButton(
            ts_row,
            text="Chapters",
            width=90,
            command=self.load_chapters_into_timestamps
        ).pack(side="left", padx=(8, 0))

        ctk.CTkButton(
            ts_row,
            text="Clear",
            width=70,
            command=self.clear_timestamps
        ).pack(side="left", padx=(8, 0))
        
        self.ts_list = ctk.CTkTextbox(ts_frame, wrap="none", height=240)
        self.ts_list.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        
        # Bind Delete key to remove the current line
        self.ts_list.bind("<Delete>", self._on_ts_delete_key)
        # Optional: Backspace too (feels natural)
        self.ts_list.bind("<BackSpace>", self._on_ts_delete_key)

        del_row = ctk.CTkFrame(ts_frame)
        del_row.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(del_row, text="Delete selected line", command=self.delete_selected_timestamp).pack(side="left")

        hint = ctk.CTkLabel(
            ts_frame,
            text="Tip: Add 00:00:00 as your first chapter. If you do not, I add it automatically.",
            anchor="w",
            text_color="#9aa0a6"
        )
        hint.pack(fill="x", padx=10, pady=(0, 10))

        # Encoder settings (right)
        enc = ctk.CTkFrame(mid)
        enc.pack(side="left", fill="both", expand=True, padx=(6, 10), pady=10)

        ctk.CTkLabel(enc, text="Encoder settings", anchor="w").grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 8), sticky="w")

        # Container
        ctk.CTkLabel(enc, text="Container", anchor="w").grid(row=1, column=0, padx=10, pady=6, sticky="w")
        self.container_opt = ctk.CTkOptionMenu(enc, values=["mkv", "mp4"])
        self.container_opt.grid(row=1, column=1, padx=10, pady=6, sticky="ew")
        self.container_opt.set(self.settings.get("container", "mkv"))

        # Video codec
        ctk.CTkLabel(enc, text="Video codec", anchor="w").grid(row=2, column=0, padx=10, pady=6, sticky="w")
        self.vcodec_opt = ctk.CTkOptionMenu(
            enc,
            values=[
                "libx264",
                "libx265",
                "h264_nvenc",
                "hevc_nvenc",
                "copy",
            ]
        )

        self.vcodec_opt.grid(row=2, column=1, padx=10, pady=6, sticky="ew")
        self.vcodec_opt.set(self.settings.get("vcodec", "libx264"))

        def _on_vcodec_change(choice: str):
            # NVENC uses CQ, not CRF (but we reuse the same box)
            if choice in ("h264_nvenc", "hevc_nvenc"):
                # optional: nudge toward sane defaults if the box is empty
                cur = (self.crf_entry.get() or "").strip()
                if not cur:
                    self.crf_entry.insert(0, "20")  # CQ 20 default

            # If user picked stream copy for video, preset/crf don't matter much
            # (we just leave the UI alone)

        self.vcodec_opt.configure(command=_on_vcodec_change)

        # Preset
        ctk.CTkLabel(enc, text="Preset", anchor="w").grid(row=3, column=0, padx=10, pady=6, sticky="w")
        self.preset_opt = ctk.CTkOptionMenu(
            enc,
            values=[
                "ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow",
                "p1","p2","p3","p4","p5","p6","p7"
            ]
        )

        self.preset_opt.grid(row=3, column=1, padx=10, pady=6, sticky="ew")
        self.preset_opt.set(self.settings.get("preset", "medium"))

        # CRF
        ctk.CTkLabel(enc, text="CRF", anchor="w").grid(row=4, column=0, padx=10, pady=6, sticky="w")
        self.crf_entry = ctk.CTkEntry(enc, placeholder_text="CRF (x264/x265) or CQ (NVENC)")
        self.crf_entry.grid(row=4, column=1, padx=10, pady=6, sticky="ew")
        self.crf_entry.insert(0, str(self.settings.get("crf", 20)))

        # Audio
        ctk.CTkLabel(enc, text="Audio", anchor="w").grid(row=5, column=0, padx=10, pady=6, sticky="w")
        self.acodec_opt = ctk.CTkOptionMenu(enc, values=["aac", "copy"])
        self.acodec_opt.grid(row=5, column=1, padx=10, pady=6, sticky="ew")
        self.acodec_opt.set(self.settings.get("acodec", "aac"))

        ctk.CTkLabel(enc, text="Audio bitrate", anchor="w").grid(row=6, column=0, padx=10, pady=6, sticky="w")
        self.abitrate_entry = ctk.CTkEntry(enc, placeholder_text="e.g. 192k")
        self.abitrate_entry.grid(row=6, column=1, padx=10, pady=6, sticky="ew")
        self.abitrate_entry.insert(0, str(self.settings.get("abitrate", "192k")))
        
        # Fast split + subs
        self.fast_split_var = ctk.BooleanVar(value=bool(self.settings.get("fast_split", True)))
        self.include_subs_var = ctk.BooleanVar(value=bool(self.settings.get("include_subs", True)))

        ctk.CTkCheckBox(enc, text="Fast split (copy streams, no re-encode)", variable=self.fast_split_var)\
            .grid(row=10, column=0, columnspan=2, padx=10, pady=(10, 4), sticky="w")

        ctk.CTkCheckBox(enc, text="Include subtitle tracks", variable=self.include_subs_var)\
            .grid(row=11, column=0, columnspan=2, padx=10, pady=(4, 10), sticky="w")

        # Show title
        ctk.CTkLabel(enc, text="Show title", anchor="w").grid(row=7, column=0, padx=10, pady=6, sticky="w")
        self.show_title_entry = ctk.CTkEntry(enc, placeholder_text="e.g. Pokémon the Series XYZ")
        self.show_title_entry.grid(row=7, column=1, padx=10, pady=6, sticky="ew")
        self.show_title_entry.insert(0, str(self.settings.get("show_title", "")))

        # Season number
        ctk.CTkLabel(enc, text="Season", anchor="w").grid(row=8, column=0, padx=10, pady=6, sticky="w")
        self.season_entry = ctk.CTkEntry(enc, placeholder_text="e.g. 19")
        self.season_entry.grid(row=8, column=1, padx=10, pady=6, sticky="ew")
        self.season_entry.insert(0, str(self.settings.get("season", "1")))

        # Start episode number
        ctk.CTkLabel(enc, text="Start EP #", anchor="w").grid(row=9, column=0, padx=10, pady=6, sticky="w")
        self.start_ep_entry = ctk.CTkEntry(enc, placeholder_text="e.g. 1")
        self.start_ep_entry.grid(row=9, column=1, padx=10, pady=6, sticky="ew")
        self.start_ep_entry.insert(0, str(self.settings.get("start_ep", "1")))


        enc.grid_columnconfigure(1, weight=1)

        # ----------------------------
        # Bottom: progress + buttons + log
        # ----------------------------
        bottom = ctk.CTkFrame(root)
        bottom.pack(fill="x", padx=10, pady=(8, 10))

        btn_row = ctk.CTkFrame(bottom)
        btn_row.pack(fill="x", padx=10, pady=(10, 6))

        self.start_btn = ctk.CTkButton(btn_row, text="Start encode", command=self.start_encode)
        self.start_btn.pack(side="left")

        self.stop_btn = ctk.CTkButton(btn_row, text="Stop", fg_color="#aa0000", hover_color="#880000", command=self.stop_encode, state="disabled")
        self.stop_btn.pack(side="left", padx=(10, 0))

        self.progress = ctk.CTkProgressBar(bottom)
        self.progress.pack(fill="x", padx=10, pady=(6, 6))
        self.progress.set(0)

        self.status_lbl = ctk.CTkLabel(bottom, text="Ready.", anchor="w")
        self.status_lbl.pack(fill="x", padx=10, pady=(0, 8))

        self.log_box = ctk.CTkTextbox(root, height=160)
        self.log_box.pack(fill="both", expand=False, padx=20, pady=(0, 10))
        self.log_box.configure(state="disabled")

        # Restore last paths
        if self.settings.get("last_input"):
            self.in_entry.insert(0, self.settings["last_input"])
        if self.settings.get("last_output"):
            self.out_entry.insert(0, self.settings["last_output"])

    def _on_ts_delete_key(self, event=None):
        # If user highlighted a block, delete those lines
        if self._delete_selected_lines_if_any():
            return "break"

        # Otherwise delete the current cursor line
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



    def stop_encode(self):
        self._stop_flag.set()
        self._log("Stop requested...")

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
        """
        total_work_ms = sum((e - s) for (s, e) in segments)
        done_work_ms = 0

        ffmpeg_path, ffprobe_path = get_ffmpeg_tools()
        if not ffmpeg_path:
            self._ui_queue.put(("done", False, "ffmpeg not found.", None))
            return

        def push_progress(done_ms: int, msg: str):
            pct = 0.0 if total_work_ms <= 0 else max(0.0, min(1.0, done_ms / total_work_ms))
            self._ui_queue.put(("progress", pct, msg))

        for idx, (start_ms, end_ms) in enumerate(segments, start=0):
            if self._stop_flag.is_set():
                self._ui_queue.put(("done", False, "Stopped by user.", None))
                return

            ep_num = start_ep_num + idx
            safe_title = re.sub(r'[<>:"/\\|?*]+', '', (show_title or "")).strip() or stem
            out_name = f"{safe_title} - S{int(season_num):02d}E{ep_num:02d}.{container}"

            out_path = os.path.join(out_dir, out_name)

            ss = ms_to_hhmmss(start_ms)
            to = ms_to_hhmmss(end_ms)

            fast_split = ("-c:v" in base_cmd) and (base_cmd[base_cmd.index("-c:v") + 1] == "copy")

            cmd = base_cmd.copy()

            seg_dur_ms = max(1, end_ms - start_ms)
            t = ms_to_hhmmss(seg_dur_ms)
            ss = ms_to_hhmmss(start_ms)

            if fast_split:
                # Build fast keyframe-aligned cut: -ss before -i
                # Reuse everything from base_cmd AFTER the input (maps + codecs + subtitle settings)
                tail = base_cmd.copy()
                i_pos = tail.index("-i")
                tail = tail[i_pos + 2:]  # everything after input path

                cmd = [ffmpeg_path, "-y", "-ss", ss, "-i", in_path] + tail + ["-t", t]
            else:
                # Accurate path: -ss after -i (your robust patch)
                try:
                    i_pos = cmd.index("-i")
                    insert_at = i_pos + 2
                    cmd[insert_at:insert_at] = ["-ss", ss, "-t", t]
                except ValueError:
                    cmd = [ffmpeg_path, "-y", "-i", in_path, "-ss", ss, "-t", t] + cmd[3:]

            # progress output
            cmd += ["-progress", "pipe:1", "-nostats", out_path]

            self._ui_queue.put(("log", f"\nEpisode {ep_num:02d}: {ss} -> {to}"))
            self._ui_queue.put(("log", f"Writing: {out_path}"))

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

            seg_len_ms = seg_dur_ms
            seg_last_pct = 0.0

            if fast_split:
                push_progress(done_work_ms, f"Splitting episode {ep_num:02d} (copy)...")

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

                if (not fast_split) and line.startswith("out_time_ms="):
                    try:
                        out_ms = int(line.split("=", 1)[1].strip())
                        seg_pct = max(0.0, min(1.0, out_ms / seg_len_ms))
                        # smooth overall progress
                        overall_done = done_work_ms + int(seg_pct * seg_len_ms)
                        if abs(seg_pct - seg_last_pct) >= 0.002:
                            seg_last_pct = seg_pct
                            push_progress(overall_done, f"Encoding episode {ep_num:02d}... {seg_pct*100:.1f}%")
                    except Exception:
                        pass

            rc = proc.wait()
            if rc != 0:
                if self._stop_flag.is_set():
                    self._ui_queue.put(("done", False, "Stopped by user.", None))
                else:
                    self._ui_queue.put(("done", False, f"ffmpeg failed on episode {ep_num:02d} (code {rc}).", None))
                return

            done_work_ms += seg_len_ms
            push_progress(done_work_ms, f"Finished episode {ep_num:02d}")

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
        self.start_btn.configure(state="disabled" if running else "normal")
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

        save_settings(self.settings)
        self.destroy()


if __name__ == "__main__":
    app = EpisodeEncoderApp()
    app.mainloop()

