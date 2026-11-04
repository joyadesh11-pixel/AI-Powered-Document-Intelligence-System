// Client-side pre-check limits. Advisory only: the server is authoritative and these mirror
// backend defaults (MAX_FILE_MB=25, MAX_FILES_PER_REQUEST=20, ingest._ALLOWED_EXT).
export const ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xlsx", ".xls", ".csv"];
export const MAX_FILE_MB = 25;
export const MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024;
export const MAX_FILES_PER_REQUEST = 20;

export function extensionOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}

/** Returns a rejection reason, or null when the file looks acceptable. */
export function precheck(file: File): string | null {
  if (!ALLOWED_EXTENSIONS.includes(extensionOf(file.name))) {
    return `Unsupported type (allowed: ${ALLOWED_EXTENSIONS.join(" ")})`;
  }
  if (file.size === 0) return "File is empty";
  if (file.size > MAX_FILE_BYTES) return `Larger than ${MAX_FILE_MB} MB`;
  return null;
}
