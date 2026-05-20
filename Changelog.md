# VisionSplit Changelog v2.0

## Major UI Refresh

### Futuristic VisionSplit Interface

* Rebuilt the main VisionSplit interface with a cleaner futuristic dark theme
* Added a more polished Vision software-style layout
* Added organized card-based sections for splitting, encoding, stitching, clip export, and stereo tools
* Improved spacing, readability, and overall workflow clarity
* Added a scroll-safe main layout to prevent UI elements from getting cut off on smaller screens
* Added a branded header area with VisionSplit title, subtitle, and local FFmpeg workflow messaging

---

## New Smart TV / Anime Split Tools

### Episode Length Timestamp Generator

* Added a Smart TV / Anime split section
* Users can now enter an episode length and episode count to auto-generate split timestamps
* Useful for anime DVDs or merged TV show files where multiple episodes are stored in one video
* Automatically keeps `00:00:00` as the first split point when needed

### Chapter Interval Splitting

* Added an option to use every Nth chapter as an episode start
* Added chapter offset support for DVDs with intros, logos, previews, or non-standard chapter layouts
* Added preview support so users can inspect split points before rendering

---

## Timestamp Editing Improvements

* Fixed timestamp list editing behavior
* Backspace and Delete now work like normal text editing inside the timestamp list
* Added Ctrl + Delete behavior for deleting full timestamp lines
* Kept the Delete Selected Line button for simple manual timestamp removal

---

## Single Clip Export

### Movie Clip Export Tool

* Added a dedicated Single Clip Export section
* Users can now load a source movie or video and export one clip using start and end timestamps
* Added custom clip naming
* Supports the existing container, codec, audio, and quality settings
* Supports both fast stream-copy style export and accurate re-encode export

---

## Stereo SBS Tools

### Full SBS Split

* Added a new Stereo SBS tools section
* Users can now load a Full SBS 3D video as the main input and export separate left-eye and right-eye files
* Outputs left and right eye videos automatically using clean naming

### Left / Right Eye Merge

* Added support for selecting separate left-eye and right-eye videos
* Users can merge two eye videos back into a Full SBS 3D video
* Uses FFmpeg filter processing for accurate stereo stacking
* Falls back safely when stream copy is not possible because crop and stack operations require re-encoding

---

## Progress and Render Status Improvements

### Always-Visible Render Status

* Moved render progress into the Terminal Log area
* Progress bar and status text now stay visible while users scroll through the main interface
* Improved visibility for long-running split, stitch, clip export, and stereo operations
* Render status now feels more connected to FFmpeg output and debug logging

### Better Worker Progress

* Improved progress handling for split encoding
* Improved progress handling for single clip export
* Improved progress handling for stitch operations
* Added progress support for Stereo SBS split and merge operations
* Improved FFmpeg progress parsing for builds that report timing differently

---

## Clip Stitcher Improvements

* Kept the clip stitcher workflow inside the new interface
* Added cleaner controls for adding, removing, moving, and clearing clips
* Improved the stitcher layout to better fit the updated VisionSplit UI
* Stitch jobs continue to support copy mode and re-encode mode depending on selected settings

---

## Encoding and FFmpeg Workflow

* Continued support for FFmpeg and FFprobe detection
* Supports FFmpeg beside the executable, bundled builds, or system PATH
* Preserved encoder options for:

  * `libx264`
  * `libx265`
  * `h264_nvenc`
  * `hevc_nvenc`
  * `copy`

* Preserved audio options for AAC and stream copy
* Preserved subtitle inclusion support
* Preserved fast split mode for no re-encode workflows

---

## Stability and Workflow Fixes

* Restored missing Stop Active Job function after the UI/tooling changes
* Improved button state handling while jobs are running
* Prevented multiple render actions from being started at the same time
* Preserved queue-based UI updates for thread-safe progress and logging
* Continued saving user settings between sessions

---

## Summary

VisionSplit v1.1.0 is a major usability and workflow update focused on making the app feel more like a complete premium video utility. This release adds smart anime/TV splitting, single clip export, Full SBS stereo split/merge tools, a redesigned futuristic interface, and improved always-visible progress feedback.
