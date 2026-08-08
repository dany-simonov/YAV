import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const accountMock = vi.hoisted(() => ({
  get: vi.fn(),
  createEmailPasswordSession: vi.fn(),
  deleteSession: vi.fn(),
}));
const ensureUserProfile = vi.hoisted(() => vi.fn());

vi.mock('../lib/appwrite', () => ({
  account: accountMock,
  ID: { unique: vi.fn(() => 'new-user') },
}));
vi.mock('../lib/serverProfile', () => ({ ensureUserProfile }));

import { useAuthStore } from './authStore';

const appwriteUser = {
  $id: 'user-1',
  $createdAt: '2026-08-08T12:00:00.000Z',
  email: 'user@example.test',
  name: 'User',
  emailVerification: false,
  phone: '',
  phoneVerification: false,
};

describe('profile bootstrap failure', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, 'log').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    accountMock.deleteSession.mockResolvedValue({});
    useAuthStore.setState({
      user: null,
      session: null,
      isLoading: true,
      isActionLoading: false,
      error: null,
      isInitialized: false,
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it('finishes initialization and discards an unusable authenticated session', async () => {
    accountMock.get.mockResolvedValue(appwriteUser);
    ensureUserProfile.mockRejectedValue(new Error('profile unavailable'));

    await useAuthStore.getState().initialize();

    expect(useAuthStore.getState()).toMatchObject({
      user: null,
      isLoading: false,
      isInitialized: true,
    });
    expect(accountMock.deleteSession).toHaveBeenCalledWith('current');
  });

  it('finishes login action and removes the newly-created session', async () => {
    accountMock.createEmailPasswordSession.mockResolvedValue({ $id: 'session-1' });
    accountMock.get.mockResolvedValue(appwriteUser);
    ensureUserProfile.mockRejectedValue(new Error('profile unavailable'));

    const result = await useAuthStore.getState().login('user@example.test', 'password');

    expect(result.success).toBe(false);
    expect(useAuthStore.getState().isActionLoading).toBe(false);
    expect(accountMock.deleteSession).toHaveBeenCalledWith('current');
  });
});
