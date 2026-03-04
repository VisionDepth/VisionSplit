<h1 align="center">VisionSplit</h1>

<p align="center">
  <img src="https://github.com/user-attachments/assets/fee33480-9b70-45ae-ba1d-f427e358746e" width="520">
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
- Fast stream copy mode (no re-encoding)
- Optional re-encoding with configurable codecs
- MKV and MP4 output support
- Automatic episode naming (S01E01 format)
- Subtitle track support
- Simple GUI built with CustomTkinter
- FFmpeg bundled with the release

---

## Typical Workflow

1. Rip the disc using MakeMKV  
2. Open VisionSplit  
3. Load the disc file  
4. Click **Chapters** to import chapter timestamps  
5. Adjust timestamps to match each episode start and delete unnecessary ones  
6. Click **Start Encode**

Episodes are generated automatically.

---

## Download

Download the latest release from the **Releases** page.

Extract the ZIP and run:

```
VisionSplit.exe
```

No installation required.

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
