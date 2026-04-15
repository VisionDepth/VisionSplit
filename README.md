<h1 align="center">VisionSplit</h1>

<p align="center">
  <img src="https://github.com/user-attachments/assets/7be8afdb-6264-41a9-8e09-624577f19252" width="520">
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
- Fast stream copy mode for quick splitting without re-encoding
- Optional re-encoding with configurable codec settings
- Stitch multiple clips together into one merged video
- Reorder clips before stitching
- MKV and MP4 output support
- Automatic episode naming in S01E01 format
- Subtitle track support
- Simple GUI built with CustomTkinter
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
7. Click **Start Encode**

VisionSplit will automatically generate separate episode files.

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

```
VisionSplit.exe
```

No installation required.

---

## Run From Source

If you prefer to run VisionSplit directly from the source code:

### 1. Clone the repository and create environment (Conda Recommended)
```
# conda create -n VisionSplit python=3.13 (uncomment if using conda environment)
# conda activate VisionSplit

git clone https://github.com/VisionDepth/VisionSplit.git

cd VisionSplit
```
### 2. Install Python dependencies
```
pip install -r requirements.txt
```

### 3. Install FFmpeg

VisionSplit requires **FFmpeg** and **ffprobe** to be available on your system.

Download FFmpeg from:  
https://ffmpeg.org/download.html

After installing, make sure `ffmpeg` and `ffprobe` are available in your system **PATH**.

You can verify this by running:
```
ffmpeg -version
```

### 4. Run VisionSplit
```
python VisionSplit.py
```
The application window will launch.

---

## Encoder Options

VisionSplit supports both fast stream copying and full re-encoding.

### Fast Split Mode
- Copies video/audio streams directly
- Extremely fast
- No quality loss
- Cuts occur on keyframes

### Re-Encode Mode
- Accurate frame-perfect splits
- Supports CPU encoders:
  - libx264
  - libx265
- Supports NVIDIA GPU encoders:
  - h264_nvenc
  - hevc_nvenc

---

## Credits

VisionSplit uses **FFmpeg** for video processing.

FFmpeg is licensed under LGPL/GPL depending on build.

https://ffmpeg.org

---

## Feedback

This is an early release and feedback is welcome.

If you encounter bugs or have feature ideas, please open an issue.
