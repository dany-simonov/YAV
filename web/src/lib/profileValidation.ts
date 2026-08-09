const UNSAFE_NAME_CHARS = /[\u0000-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/u;

export function normalizeDisplayName(value: string): string | null {
  const normalized = value.normalize('NFC').trim();
  if (normalized.length < 2 || normalized.length > 50 || UNSAFE_NAME_CHARS.test(normalized)) {
    return null;
  }
  return normalized;
}
