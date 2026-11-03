# node: image_prep
**Purpose.** Turn images, TIFF frames and scanned PDF pages into clean page images, and score their quality.
**Reads** `file_type`, `file_path`, `scanned_pages` (PDF) or `page_count` (image). **Writes** `page_images: dict[int, path]` (OCR-ready grayscale PNG in a temp dir), `image_quality: dict[int, {blur, side_px, contrast}]`, `scanned_pages` (all pages for image files), `low_conf_pages` (pages too poor for OCR).
## Algorithm
- PDF pages: `page.get_pixmap(dpi=OCR_DPI)`; cap the long side at `OCR_MAX_SIDE_PX`.
- Images: PIL open, `ImageOps.exif_transpose`, TIFF: iterate frames (`seek`). Multi-frame = multiple pages.
- Grayscale, autocontrast; deskew if estimated skew between 0.5 and 15 degrees (OpenCV `minAreaRect` on binarised text pixels).
- Quality: blur = variance of Laplacian; `side_px` = long side; contrast = std of gray levels. **Measured on the post-deskew array** (bilinear `warpAffine` introduces a slight blur, so stored blur is a few % lower than the raw image; the gate is still applied to the processed value because that is what the OCR engine actually receives).
- If `blur < BLUR_VAR_MIN` or `side_px < MIN_SIDE_PX`: add page to `low_conf_pages` with issue `LOW_IMAGE_QUALITY`; skip OCR for it (goes straight to vision).
**Fallbacks.** A frame that fails to decode -> skip it, warn; if all frames fail -> `CORRUPT_FILE` via error. Colour copy for vision is produced on demand by `vision_extract`, not here.
**Edge.** Do not store image bytes in state. Temp dir cleaned by the caller (`process()` context manager).
## Tests
- T1 PNG/JPG -> one page image, grayscale. T2 3-frame TIFF -> pages 0-2.
- T3 EXIF-rotated JPG -> upright. T4 `rotated_5deg.png` -> skew corrected (OCR text found later).
- T5 `blurry_severe.png` -> in `low_conf_pages`. T6 tiny 300px image -> `LOW_IMAGE_QUALITY`.
- T7 scanned PDF pages rendered at expected pixel size. T8 no bytes in state (assert `json.dumps` of serialisable keys works).
**Done when** T1-T8 pass.
