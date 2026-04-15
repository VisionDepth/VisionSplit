# VisionSplit v1.1 - Changelog

VisionSplit v1.1 expands the app beyond timestamp-based splitting by introducing a new clip stitching workflow, making it easier to combine multiple clips into a single final output.

## New Features

### Clip Stitcher
- Added a dedicated **Clip Stitcher** panel to the interface
- Added support for loading multiple video clips into a stitch list
- Added clip order management tools:
  - Add clips
  - Remove selected clip
  - Move clip up
  - Move clip down
  - Clear clip list
- Added **Start Stitch** button for exporting stitched clips into one video

## Stitching Support
- Added FFmpeg-based clip concatenation workflow
- Supports fast stitching with **stream copy** when clips are already compatible
- Supports re-encoding when needed for broader compatibility
- Uses selected output container and encoder settings for final stitched export

## Workflow Improvements
- VisionSplit now supports both major workflows in one app:
  - **Split** a source video using timestamps
  - **Stitch** multiple clips together in a custom order
- Makes the app more flexible for episode prep, clip organization, and final export assembly

## Compatibility Notes
- Fast stitch mode is ideal when clips share matching video and audio properties
- Clips with different codecs, frame rates, resolutions, or audio settings may require re-encoding