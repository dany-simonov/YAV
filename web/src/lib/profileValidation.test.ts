import { describe, expect, it } from 'vitest';

import { normalizeDisplayName } from './profileValidation';

describe('normalizeDisplayName', () => {
  it('normalizes ordinary Unicode names without changing legitimate characters', () => {
    expect(normalizeDisplayName('  Имя-Тест  ')).toBe('Имя-Тест');
  });

  it.each(['A\u0000B', 'A\u202EB', 'A\u2066B'])('rejects unsafe display characters', (name) => {
    expect(normalizeDisplayName(name)).toBeNull();
  });
});
