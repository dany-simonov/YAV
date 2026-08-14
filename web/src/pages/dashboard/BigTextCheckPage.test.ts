import { describe, expect, it } from 'vitest';

import {
  buildComplexPayload,
  COMPLEX_MAX_LENGTH,
  COMPLEX_MIN_LENGTH,
  COMPLEX_ANALYSIS_ROUTE,
  isComplexTextSubmittable,
} from '../../lib/complexAnalysis';
import { BIG_TEXT_REDIRECT_TARGET } from './BigTextCheckPage';

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
    expect(buildComplexPayload(text, user)).not.toHaveProperty('url');
    expect(buildComplexPayload(text, user)).not.toHaveProperty('fileId');
    expect(buildComplexPayload(text, user)).not.toHaveProperty('articleUrl');
    expect(buildComplexPayload(text, user)).not.toHaveProperty('complexFiles');
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

  it('keeps the retired big-text URL as an alias for the Complex tab', () => {
    expect(COMPLEX_ANALYSIS_ROUTE).toBe('/dashboard/check?tab=complex');
    expect(BIG_TEXT_REDIRECT_TARGET).toBe(COMPLEX_ANALYSIS_ROUTE);
  });
});
