import { account, type Models } from './appwrite';

export interface EmailVerificationToken {
  userId: string;
  secret: string;
}

const CALLBACK_PATH = '/verify-email/callback';
const TOKEN_KEYS = ['userId', 'secret'] as const;
const MAX_USER_ID_LENGTH = 36;
const MAX_SECRET_LENGTH = 256;

export function buildEmailVerificationUrl(origin = window.location.origin): string {
  const url = new URL('/', origin);
  url.hash = CALLBACK_PATH;
  return url.toString();
}

export function sendEmailVerification(
  url = buildEmailVerificationUrl()
): Promise<Models.Token> {
  return account.createEmailVerification({ url });
}

export function resendEmailVerification(
  url = buildEmailVerificationUrl()
): Promise<Models.Token> {
  return sendEmailVerification(url);
}

export function confirmEmailVerification(
  token: EmailVerificationToken
): Promise<Models.Token> {
  return account.updateEmailVerification({
    userId: token.userId,
    secret: token.secret,
  });
}

function tokenFromParams(params: URLSearchParams): EmailVerificationToken | null {
  if (TOKEN_KEYS.some((key) => params.getAll(key).length !== 1)) return null;
  const userId = params.get('userId')?.trim() || '';
  const secret = params.get('secret')?.trim() || '';
  return userId && secret && userId.length <= MAX_USER_ID_LENGTH && secret.length <= MAX_SECRET_LENGTH
    ? { userId, secret }
    : null;
}

function removeTokenParams(params: URLSearchParams): void {
  TOKEN_KEYS.forEach((key) => params.delete(key));
}

/** Read a verification token from normal or HashRouter query params and erase it from history. */
export function consumeEmailVerificationToken(
  href = window.location.href,
  replaceState: (data: unknown, unused: string, url?: string | URL | null) => void =
    window.history.replaceState.bind(window.history),
  historyState: unknown = window.history.state
): EmailVerificationToken | null {
  const url = new URL(href);
  const hash = url.hash.startsWith('#') ? url.hash.slice(1) : url.hash;
  const queryIndex = hash.indexOf('?');
  const hashPath = queryIndex >= 0 ? hash.slice(0, queryIndex) : hash;
  const hashParams = new URLSearchParams(queryIndex >= 0 ? hash.slice(queryIndex + 1) : '');
  const token = tokenFromParams(url.searchParams) || tokenFromParams(hashParams);

  removeTokenParams(url.searchParams);
  removeTokenParams(hashParams);
  const cleanHashQuery = hashParams.toString();
  url.hash = `${hashPath}${cleanHashQuery ? `?${cleanHashQuery}` : ''}`;
  replaceState(historyState, '', url.toString());

  return token;
}
