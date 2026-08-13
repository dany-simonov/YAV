import { describe, expect, it } from 'vitest';

import { displayAuthenticityIndex, displayModelName } from './resultPresentation';

describe('result presentation', () => {
  it('uses canonical authenticity_index without verdict-based inversion', () => {
    expect(displayAuthenticityIndex(95, 0.95, 'REAL')).toBe(95);
    expect(displayAuthenticityIndex(10, 0.95, 'FAKE')).toBe(10);
  });

  it('uses legacy confidence conversion only when canonical index is absent', () => {
    expect(displayAuthenticityIndex(null, 0.95, 'FAKE')).toBe(5);
  });

  it.each([0, 100])('preserves canonical boundary %i', (index) => {
    expect(displayAuthenticityIndex(index, 0.95, 'FAKE')).toBe(index);
  });

  it('formats the Gemini VIDEO identifier for users', () => {
    expect(displayModelName('gemini_video_verification')).toBe('Gemini Video Verification');
  });

  it('formats the Gemini TEXT identifier for users', () => {
    expect(displayModelName('gemini_text_verification')).toBe('Gemini Text Verification');
  });
});
