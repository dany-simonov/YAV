/**
 * Utility Functions
 * =================
 */

/**
 * Combines class names, filtering out falsy values
 */
export function cn(...classes: (string | undefined | null | false)[]): string {
  return classes.filter(Boolean).join(' ');
}

/**
 * Format date to locale string
 */
export function formatDate(date: string | Date, locale = 'ru-RU'): string {
  return new Date(date).toLocaleDateString(locale, {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

/**
 * Format milliseconds to human readable string
 */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}мс`;
  return `${(ms / 1000).toFixed(1)}с`;
}

/**
 * Truncate string with ellipsis
 */
export function truncate(str: string, length: number): string {
  if (str.length <= length) return str;
  return str.slice(0, length) + '...';
}

/**
 * Get file size in human readable format
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 Б';
  
  const k = 1024;
  const sizes = ['Б', 'КБ', 'МБ', 'ГБ'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

/** Basic syntax check; domain labels and the top-level domain are checked below. */
export const EMAIL_REGEX = /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+$/i;

export function normalizeEmail(email: string): string {
  return email.replace(/\s+/g, '').toLowerCase();
}

/**
 * Validate email format
 */
export function isValidEmail(email: string): boolean {
  const value = normalizeEmail(email);
  if (!value || value.length > 254 || !EMAIL_REGEX.test(value)) return false;

  const [localPart, domain, ...extraParts] = value.split('@');
  if (!localPart || !domain || extraParts.length > 0 || localPart.length > 64) return false;
  if (localPart.startsWith('.') || localPart.endsWith('.') || localPart.includes('..')) return false;

  const labels = domain.split('.');
  if (labels.length < 2) return false;
  if (!/^[a-z]{2,24}$/i.test(labels[labels.length - 1] ?? '')) return false;

  return labels.every((label) =>
    label.length > 0 &&
    label.length <= 63 &&
    /^[a-z0-9-]+$/i.test(label) &&
    !label.startsWith('-') &&
    !label.endsWith('-')
  );
}

/**
 * Validate password strength
 */
export function validatePassword(password: string): { valid: boolean; message?: string } {
  if (password.length < 8) {
    return { valid: false, message: 'Минимум 8 символов' };
  }
  return { valid: true };
}
