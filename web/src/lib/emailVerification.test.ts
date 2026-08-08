import { beforeEach, describe, expect, it, vi } from 'vitest';

const accountMock = vi.hoisted(() => ({
  createEmailVerification: vi.fn(),
  updateEmailVerification: vi.fn(),
}));

vi.mock('./appwrite', () => ({ account: accountMock }));

import {
  buildEmailVerificationUrl,
  confirmEmailVerification,
  consumeEmailVerificationToken,
  sendEmailVerification,
} from './emailVerification';

describe('email verification helpers', () => {
  beforeEach(() => vi.clearAllMocks());

  it('builds a HashRouter callback URL and uses the current SDK methods', async () => {
    accountMock.createEmailVerification.mockResolvedValue({});
    accountMock.updateEmailVerification.mockResolvedValue({});

    expect(buildEmailVerificationUrl('https://example.test')).toBe(
      'https://example.test/#/verify-email/callback'
    );
    await sendEmailVerification('https://example.test/#/verify-email/callback');
    await confirmEmailVerification({ userId: 'user-1', secret: 'secret-1' });

    expect(accountMock.createEmailVerification).toHaveBeenCalledWith({
      url: expect.stringContaining('#/verify-email/callback'),
    });
    expect(accountMock.updateEmailVerification).toHaveBeenCalledWith({
      userId: 'user-1',
      secret: 'secret-1',
    });
  });

  it('consumes top-level query parameters and removes sensitive values', () => {
    const replaceState = vi.fn();
    const token = consumeEmailVerificationToken(
      'https://example.test/?userId=user-1&secret=secret-1#/verify-email/callback',
      replaceState,
      { preserved: true }
    );

    expect(token).toEqual({ userId: 'user-1', secret: 'secret-1' });
    const cleanUrl = String(replaceState.mock.calls[0][2]);
    expect(cleanUrl).toBe('https://example.test/#/verify-email/callback');
    expect(cleanUrl).not.toContain('secret-1');
    expect(cleanUrl).not.toContain('user-1');
  });

  it('supports parameters inside the hash and preserves unrelated query values', () => {
    const replaceState = vi.fn();
    const token = consumeEmailVerificationToken(
      'https://example.test/#/verify-email/callback?source=email&userId=user-2&secret=secret-2',
      replaceState,
      null
    );

    expect(token).toEqual({ userId: 'user-2', secret: 'secret-2' });
    expect(String(replaceState.mock.calls[0][2])).toBe(
      'https://example.test/#/verify-email/callback?source=email'
    );
  });

  it('returns null for missing parameters while still cleaning partial tokens', () => {
    const replaceState = vi.fn();
    expect(
      consumeEmailVerificationToken(
        'https://example.test/?userId=user-1#/verify-email/callback',
        replaceState,
        null
      )
    ).toBeNull();
    expect(String(replaceState.mock.calls[0][2])).not.toContain('userId');
  });
});
