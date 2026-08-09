export function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 2048) return null;

  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}
