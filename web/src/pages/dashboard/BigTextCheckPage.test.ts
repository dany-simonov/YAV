import { describe, expect, it } from 'vitest';

import {
  buildComplexPayload,
  COMPLEX_MAX_LENGTH,
  COMPLEX_MIN_LENGTH,
  isComplexTextSubmittable,
} from './BigTextCheckPage';

const user = { $id: 'user-1', email: 'user@example.test', name: 'Иван Петров' };

describe('BigTextCheckPage Complex input contract', () => {
  it('builds the compatibility payload for valid Complex text', () => {
    const text = 'Текст '.repeat(40);
    expect(buildComplexPayload(text, user)).toMatchObject({
      text,
      userId: 'user-1',
      mediaType: 'text',
      mode: 'hybrid_text',
    });
  });

  it('accepts the exact meaningful minimum and maximum', () => {
    expect(isComplexTextSubmittable('а'.repeat(COMPLEX_MIN_LENGTH), false)).toBe(true);
    expect(isComplexTextSubmittable('а'.repeat(COMPLEX_MAX_LENGTH), false)).toBe(true);
  });

  it('rejects below-minimum, whitespace-only, max+1 and loading submissions', () => {
    expect(isComplexTextSubmittable('а'.repeat(COMPLEX_MIN_LENGTH - 1), false)).toBe(false);
    expect(isComplexTextSubmittable(' '.repeat(COMPLEX_MIN_LENGTH), false)).toBe(false);
    expect(isComplexTextSubmittable('а'.repeat(COMPLEX_MAX_LENGTH + 1), false)).toBe(false);
    expect(isComplexTextSubmittable('а'.repeat(COMPLEX_MIN_LENGTH), true)).toBe(false);
  });
});
