<h1 align="center">VisionSplit</h1>

<p align="center">
  <img src="https://github.com/user-attachments/assets/eae882ef-8a39-452e-a463-4c4927baca3a" width="520">
</p>

<p align="center">
  <img src="https://img.shields.io/github/v/release/VisionDepth/VisionSplit?style=for-the-badge">
  <img src="https://img.shields.io/github/downloads/VisionDepth/VisionSplit/total?style=for-the-badge">
  <img src="https://img.shields.io/github/stars/VisionDepth/VisionSplit?style=for-the-badge">
  <img src="https://img.shields.io/badge/platform-Windows-blue?style=for-the-badge">
</p>

<p align="center">
Fast episode splitter for multi-episode DVD and Blu-ray rips.
</p>

<p align="center">
VisionSplit solves a common problem when ripping TV discs: many discs store multiple episodes inside a single video file. VisionSplit allows you to quickly split that file into clean individual episode files using timestamps or chapter markers.
</p>
<p align="center">
No manual trimming. No complicated editing. Just fast, clean splits.
</p>

## Features

- Split a single video file into multiple episodes
- Import chapter timestamps directly from disc rips
- Smart TV / Anime split tools for merged episode files
- Generate episode timestamps by episode length and episode count
- Use every Nth chapter with offset support for messy DVD layouts
- Preview split plans before exporting
- Edit timestamp entries directly in the timestamp list
- Fast stream copy mode for quick splitting without re-encoding
- Optional re-encoding with configurable codec settings
- Export a single clip from a movie or video using start and end timestamps
- Split Full SBS 3D videos into separate left-eye and right-eye files
- Merge separate left-eye and right-eye videos into a Full SBS 3D video
- Stitch multiple clips together into one merged video
- Reorder clips before stitching
- MKV and MP4 output support
- Automatic episode naming in S01E01 format
- Subtitle track support
- Always-visible render progress and terminal log
- Futuristic CustomTkinter GUI
- FFmpeg bundled with the release

---

## Typical Workflow

### Episode Splitting

1. Rip the disc using MakeMKV
2. Open VisionSplit
3. Load the source video
4. Click **Chapters** to import chapter timestamps
5. Adjust timestamps so each one matches an episode start
6. Remove any unnecessary timestamps
7. Click **Preview** to confirm the split plan
8. Click **Start Encode**

VisionSplit will automatically generate separate episode files using the selected show title, season number, and starting episode number.

---

### Smart TV / Anime Split

Use this when multiple episodes are merged into one long video file.

1. Load the merged source video
2. Enter the episode length, for example `00:23:40`
3. Enter the number of episodes in the file
4. Click **Generate**
5. Click **Preview** to confirm the split points
6. Adjust timestamps if needed
7. Click **Start Encode**

This is useful for anime DVDs, TV discs, or any rip where multiple episodes are stored in one video.

---

### Chapter Interval Splitting

Some DVDs include chapters for openings, endings, previews, or scene breaks instead of clean episode starts.

VisionSplit includes chapter interval tools that let you:

- Use every Nth chapter as an episode start
- Apply an offset when the disc has intros, warnings, logos, or previews
- Preview the generated split plan before encoding

Example:

```text
Every 5 chapter(s), offset 0
```

If the split starts at the wrong chapter, try changing the offset to `1`, `2`, or `3`.

---

### Single Clip Export

Use this when you only want to export one clip from a movie or video.

1. Load the source video
2. Choose an output folder
3. Enter the clip start time
4. Enter the clip end time
5. Enter an optional clip name
6. Click **Export Single Clip**

Fast split mode can export clips quickly without re-encoding, but cuts may occur on keyframes. Disable fast split mode for more accurate cuts.

---

### Stereo SBS Tools

VisionSplit includes tools for working with Full SBS 3D video.

#### Split Full SBS into Left and Right Eyes

1. Load the Full SBS video as the main input
2. Choose an output folder
3. Enter an optional output name
4. Click **Split Main Full SBS into Left + Right**

VisionSplit will export:

```text
YourName_LeftEye.mkv
YourName_RightEye.mkv
```

#### Merge Left and Right Eyes into Full SBS

1. Select the left-eye video
2. Select the right-eye video
3. Choose an output folder
4. Enter an optional output name
5. Click **Merge Left + Right into Full SBS**

VisionSplit will export a new Full SBS video.

Note: Stereo split and merge operations use FFmpeg video filters, so video stream copy is not available for these operations. The video must be re-encoded.

---

### Clip Stitching

1. Open VisionSplit
2. Add the clips you want to combine
3. Arrange them in the order you want
4. Choose your output folder and encoder settings
5. Click **Start Stitch**

VisionSplit will export the clips as a single merged file.

---

## Download

Download the latest release from the **Releases** page.

Extract the ZIP and run:

```text
VisionSplit.exe
```

No installation required.

The release build includes FFmpeg tools needed for normal use.

---

## Run From Source

If you prefer to run VisionSplit directly from the source code:

### 1. Clone the repository and create environment

Conda is recommended.

```bash
# conda create -n VisionSplit python=3.13
# conda activate VisionSplit

git clone https://github.com/VisionDepth/VisionSplit.git

cd VisionSplit
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Install FFmpeg

VisionSplit requires **FFmpeg** and **ffprobe** to be available.

You can either:

- Add FFmpeg to your system PATH
- Or place `ffmpeg.exe` and `ffprobe.exe` beside `VisionSplit.py` or `VisionSplit.exe`

Download FFmpeg from:

```text
https://ffmpeg.org/download.html
```

You can verify FFmpeg by running:

```bash
ffmpeg -version
```

### 4. Run VisionSplit

```bash
python VisionSplit.py
```

The application window will launch.

---

## Encoder Options

VisionSplit supports both fast stream copying and full re-encoding.

### Fast Split Mode

- Copies video and audio streams directly
- Extremely fast
- No quality loss
- Best for quick episode splitting
- Cuts occur on keyframes

### Re-Encode Mode

- More accurate cuts
- Supports quality control
- Required for video filtering features like Stereo SBS split and merge

Supported CPU encoders:

- `libx264`
- `libx265`

Supported NVIDIA GPU encoders:

- `h264_nvenc`
- `hevc_nvenc`

Supported audio options:

- `aac`
- `copy`

---

## Render Progress and Logs

VisionSplit includes an always-visible render status area inside the Terminal Log panel.

This shows:

- Current operation status
- Progress bar
- FFmpeg output
- Warnings and errors
- Split preview reports
- Export completion messages

This makes it easier to track long-running split, stitch, clip export, and stereo processing jobs.

---

## Credits

VisionSplit uses **FFmpeg** for video processing.

FFmpeg is licensed under LGPL/GPL depending on build.

```text
https://ffmpeg.org
```

---

## Feedback

VisionSplit is actively being developed.

If you encounter bugs, workflow issues, or have feature ideas, please open an issue on GitHub.

