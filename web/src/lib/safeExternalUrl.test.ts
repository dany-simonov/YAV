import { describe, expect, it } from 'vitest';

import { safeExternalUrl } from './safeExternalUrl';

describe('safeExternalUrl', () => {
  it.each(['javascript:alert(1)', 'data:text/html,x', 'http://example.test', 'https://user:pass@example.test'])
  ('rejects unsafe URL %s', (value) => {
    expect(safeExternalUrl(value)).toBeNull();
  });

  it('accepts a HTTPS URL', () => {
    expect(safeExternalUrl('https://example.test/source')).toBe('https://example.test/source');
  });
});
