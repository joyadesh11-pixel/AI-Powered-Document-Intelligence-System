# node: intake
**Purpose.** Decide what the file really is, reject what we cannot process, compute the hash.
**Reads** `file_path`. **Writes** `file_type` (pdf|image|sheet), `file_hash`, `size_bytes`, `page_count`, `warnings`; on failure `error_code`, `error`, `status="failed"`.

## Algorithm
1. `stat`: size 0 -> `EMPTY_FILE`; > `MAX_FILE_MB` -> `TOO_LARGE`.
2. Read first 16 bytes. Trust magic bytes over extension; mismatch adds info issue `EXT_MISMATCH`.
   `%PDF` pdf · `FF D8 FF` jpg · `89 PNG` png · `II*\0`/`MM\0*` tiff · `PK\x03\x04` zip (xlsx only if `xl/workbook.xml` present) · `D0 CF 11 E0` OLE (xls only if xlrd opens it) · text with no NUL bytes in first 8 KB and ext `.csv` -> csv. Anything else -> `UNSUPPORTED_TYPE`.
3. Streaming sha256 -> `file_hash`.
4. Open test:
   - PDF: `fitz.open`; `needs_pass` and empty password fails -> `ENCRYPTED`; 0 pages -> `CORRUPT_FILE`; `page_count > MAX_PAGES` -> `TOO_MANY_PAGES`.
   - Image: PIL `open` + `load()`; TIFF `n_frames` -> `page_count`.
   - xlsx: zip bomb guard (sum of `ZipInfo.file_size` > `MAX_UNZIPPED_MB` -> `TOO_LARGE`), then `openpyxl.load_workbook(read_only=True)`. xls: `xlrd.open_workbook`. csv: decode succeeds with one of the candidate encodings.
   - Sheets: zero non-empty cells -> `EMPTY_FILE`.
**Fallbacks.** Damaged PDF: let PyMuPDF repair (it does on open); only fail if it still yields 0 pages. Truncated image: retry with `ImageFile.LOAD_TRUNCATED_IMAGES=True`; if it loads, continue with `TRUNCATED_IMAGE` warning.
**Failure.** Return `error_code` + message; router sends to `fail`. Never raises for these.
**Edge cases.** PDF renamed `.jpg` routes as PDF. `.doc` (OLE but not xls) -> `UNSUPPORTED_TYPE`. Filename never used for anything except display.

## Tests
- T1 valid pdf/png/jpg/tiff(3 frames)/xlsx/xls/csv -> correct `file_type`, `page_count`.
- T2 `pdf_renamed.jpg` -> pdf + `EXT_MISMATCH`.
- T3 `truncated.pdf` -> `CORRUPT_FILE` (or repaired with >=1 page; assert whichever the fixture was built for).
- T4 `encrypted.pdf` -> `ENCRYPTED`. T5 `empty.bin` (0 bytes) -> `EMPTY_FILE`.
- T6 `doc.docx` -> `UNSUPPORTED_TYPE`. T7 monkeypatched `MAX_FILE_MB=0` -> `TOO_LARGE`.
- T8 `zipbomb.xlsx` (small file, huge declared sizes) -> `TOO_LARGE`.
- T9 `truncated.png` -> continues with `TRUNCATED_IMAGE`. T10 same bytes -> same hash.
**Done when** T1-T10 pass and dry-run scenarios "corrupt" and "unsupported" still end `failed`.
