import { beforeEach, describe, expect, it, vi } from 'vitest';

const createExecution = vi.hoisted(() => vi.fn());

vi.mock('./appwrite', () => ({
  APPWRITE_CONFIG: { functions: { analyze: 'analyze' } },
  functions: { createExecution },
}));

import { ensureUserProfile } from './serverProfile';

describe('profile bootstrap', () => {
  beforeEach(() => vi.clearAllMocks());

  it('delegates trusted profile creation to the Function', async () => {
    createExecution.mockResolvedValue({
      responseStatusCode: 200,
      responseBody: JSON.stringify({ profile_id: 'user-1' }),
    });

    await expect(ensureUserProfile()).resolves.toBe('user-1');
    expect(createExecution).toHaveBeenCalledWith({
      functionId: 'analyze',
      body: JSON.stringify({ action: 'ensure_profile' }),
    });
  });

  it('returns a safe error when Function response is invalid', async () => {
    createExecution.mockResolvedValue({ responseStatusCode: 500, responseBody: '{}' });

    await expect(ensureUserProfile()).rejects.toThrow('Не удалось создать профиль пользователя');
  });
});
