import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const accountMock = vi.hoisted(() => ({
  get: vi.fn(),
  create: vi.fn(),
  createEmailPasswordSession: vi.fn(),
  deleteSession: vi.fn(),
}));
const ensureUserProfile = vi.hoisted(() => vi.fn());
const verificationMocks = vi.hoisted(() => ({
  sendEmailVerification: vi.fn(),
  resendEmailVerification: vi.fn(),
  confirmEmailVerification: vi.fn(),
}));

vi.mock('../lib/appwrite', () => ({
  account: accountMock,
  ID: { unique: vi.fn(() => 'new-user') },
}));
vi.mock('../lib/serverProfile', () => ({ ensureUserProfile }));
vi.mock('../lib/emailVerification', () => verificationMocks);

import { useAuthStore } from './authStore';

const appwriteUser = (verified = false, id = 'user-1') => ({
  $id: id,
  $createdAt: '2026-08-08T12:00:00.000Z',
  email: `${id}@example.test`,
  name: 'User',
  emailVerification: verified,
  phone: '',
  phoneVerification: false,
});

const resetStore = () => {
  useAuthStore.setState({
    user: null,
    session: null,
    isLoading: true,
    isActionLoading: false,
    error: null,
    isInitialized: false,
  });
};

describe('auth and email verification flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    accountMock.create.mockResolvedValue(appwriteUser(false));
    accountMock.createEmailPasswordSession.mockResolvedValue({ $id: 'session-1' });
    accountMock.deleteSession.mockResolvedValue({});
    ensureUserProfile.mockResolvedValue('user-1');
    verificationMocks.sendEmailVerification.mockResolvedValue({});
    verificationMocks.resendEmailVerification.mockResolvedValue({});
    verificationMocks.confirmEmailVerification.mockResolvedValue({});
    resetStore();
  });

  afterEach(() => vi.restoreAllMocks());

  it('registers an unverified account, keeps its session, and sends verification', async () => {
    accountMock.get.mockResolvedValue(appwriteUser(false));

    const result = await useAuthStore.getState().register('User', ' user@example.test ', 'password');

    expect(result).toMatchObject({ success: true, verificationEmailSent: true });
    expect(accountMock.create).toHaveBeenCalledWith({
      userId: 'new-user',
      email: 'user@example.test',
      password: 'password',
      name: 'User',
    });
    expect(verificationMocks.sendEmailVerification).toHaveBeenCalledOnce();
    expect(useAuthStore.getState().user?.emailVerification).toBe(false);
    expect(useAuthStore.getState().session).toEqual({ $id: 'session-1' });
  });

  it('preserves the account and session when verification delivery fails', async () => {
    accountMock.get.mockResolvedValue(appwriteUser(false));
    verificationMocks.sendEmailVerification.mockRejectedValue(new Error('provider details'));

    const result = await useAuthStore.getState().register('User', 'user@example.test', 'password');

    expect(result.success).toBe(true);
    expect(result.verificationEmailSent).toBe(false);
    expect(result.error).toBe('Не удалось выполнить подтверждение email');
    expect(useAuthStore.getState().user).not.toBeNull();
    expect(accountMock.deleteSession).not.toHaveBeenCalled();
  });

  it('does not send verification for an already verified registration result', async () => {
    accountMock.get.mockResolvedValue(appwriteUser(true));

    const result = await useAuthStore.getState().register('User', 'user@example.test', 'password');

    expect(result.user?.emailVerification).toBe(true);
    expect(verificationMocks.sendEmailVerification).not.toHaveBeenCalled();
  });

  it.each([true, false])('returns verification state after login: %s', async (verified) => {
    accountMock.get.mockResolvedValue(appwriteUser(verified));

    const result = await useAuthStore.getState().login(' user@example.test ', 'password');

    expect(result.success).toBe(true);
    expect(result.user?.emailVerification).toBe(verified);
    expect(accountMock.createEmailPasswordSession).toHaveBeenCalledWith({
      email: 'user@example.test',
      password: 'password',
    });
  });

  it('restores an unverified session without logging it out', async () => {
    accountMock.get.mockResolvedValue(appwriteUser(false));

    await useAuthStore.getState().initialize();

    expect(useAuthStore.getState()).toMatchObject({
      isInitialized: true,
      isLoading: false,
      user: { emailVerification: false },
    });
    expect(accountMock.deleteSession).not.toHaveBeenCalled();
  });

  it('discards a session only when authenticated profile bootstrap fails', async () => {
    accountMock.get.mockResolvedValue(appwriteUser(false));
    ensureUserProfile.mockRejectedValue(new Error('profile unavailable'));

    await useAuthStore.getState().initialize();

    expect(useAuthStore.getState().user).toBeNull();
    expect(accountMock.deleteSession).toHaveBeenCalledWith('current');
  });

  it('supports resend success and safe resend errors', async () => {
    useAuthStore.setState({ user: { ...appwriteUser(false), createdAt: appwriteUser(false).$createdAt } });

    await expect(useAuthStore.getState().resendVerification()).resolves.toMatchObject({
      success: true,
      verificationEmailSent: true,
    });
    verificationMocks.resendEmailVerification.mockRejectedValue(new Error('raw provider error'));
    await expect(useAuthStore.getState().resendVerification()).resolves.toEqual({
      success: false,
      error: 'Не удалось выполнить подтверждение email',
    });
  });

  it('confirms verification and refreshes the same user and profile mirror', async () => {
    accountMock.get.mockResolvedValue(appwriteUser(true));

    const result = await useAuthStore.getState().confirmVerification('user-1', 'secret');

    expect(result).toMatchObject({ success: true, sessionState: 'same' });
    expect(verificationMocks.confirmEmailVerification).toHaveBeenCalledWith({
      userId: 'user-1',
      secret: 'secret',
    });
    expect(ensureUserProfile).toHaveBeenCalled();
    expect(useAuthStore.getState().user?.emailVerification).toBe(true);
  });

  it('reports callback success without a session', async () => {
    accountMock.get.mockRejectedValue(new Error('no session'));

    await expect(
      useAuthStore.getState().confirmVerification('user-1', 'secret')
    ).resolves.toMatchObject({ success: true, sessionState: 'none' });
  });

  it('does not replace or delete another active user session', async () => {
    const other = appwriteUser(true, 'other-user');
    useAuthStore.setState({ user: { ...other, createdAt: other.$createdAt } });
    accountMock.get.mockResolvedValue(other);

    const result = await useAuthStore.getState().confirmVerification('user-1', 'secret');

    expect(result).toMatchObject({ success: true, sessionState: 'other' });
    expect(useAuthStore.getState().user?.$id).toBe('other-user');
    expect(accountMock.deleteSession).not.toHaveBeenCalled();
  });

  it('returns safe invalid callback errors', async () => {
    verificationMocks.confirmEmailVerification.mockRejectedValue(new Error('invalid secret detail'));
    accountMock.get.mockRejectedValue(new Error('no session'));

    const result = await useAuthStore.getState().confirmVerification('user-1', 'bad-secret');

    expect(result).toEqual({
      success: false,
      error: 'Не удалось выполнить подтверждение email',
      reason: 'invalid',
    });
    expect(JSON.stringify(result)).not.toContain('invalid secret detail');
  });

  it('classifies callback network failures without exposing raw errors', async () => {
    verificationMocks.confirmEmailVerification.mockRejectedValue(new Error('network failed'));
    accountMock.get.mockRejectedValue(new Error('no session'));

    await expect(
      useAuthStore.getState().confirmVerification('user-1', 'secret')
    ).resolves.toEqual({
      success: false,
      error: 'Ошибка сети. Проверьте подключение к интернету',
      reason: 'network',
    });
  });

  it('recognizes an already verified account when a callback token was reused', async () => {
    verificationMocks.confirmEmailVerification.mockRejectedValue(new Error('used token'));
    accountMock.get.mockResolvedValue(appwriteUser(true));

    await expect(
      useAuthStore.getState().confirmVerification('user-1', 'used-secret')
    ).resolves.toMatchObject({
      success: true,
      sessionState: 'same',
      alreadyVerified: true,
    });
    expect(ensureUserProfile).toHaveBeenCalled();
  });

  it('rejects callback calls with missing parameters', async () => {
    await expect(useAuthStore.getState().confirmVerification('', '')).resolves.toEqual({
      success: false,
      error: 'Ссылка подтверждения неполная',
    });
    expect(verificationMocks.confirmEmailVerification).not.toHaveBeenCalled();
  });
});
