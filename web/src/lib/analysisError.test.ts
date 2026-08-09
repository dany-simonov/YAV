import { describe, expect, it } from 'vitest';

import { analysisErrorMessage, parseAnalysisBackendError } from './analysisError';

const messageFor = (payload: unknown) => analysisErrorMessage(parseAnalysisBackendError(payload));

describe('analysis error presentation', () => {
  it('maps rate_limit_exceeded safely', () => {
    expect(messageFor({ code: 'rate_limit_exceeded' })).toBe('Слишком много запросов. Попробуйте немного позже.');
  });

  it('shows a valid rate-limit retry delay', () => {
    expect(messageFor({ code: 'rate_limit_exceeded', retry_after: 17 })).toBe(
      'Слишком много запросов. Попробуйте снова через 17 сек.'
    );
  });

  it('maps daily and monthly quota errors', () => {
    expect(messageFor({ code: 'daily_quota_exceeded' })).toBe('Дневной лимит проверок исчерпан.');
    expect(messageFor({ code: 'monthly_quota_exceeded' })).toBe('Месячный лимит проверок исчерпан.');
  });

  it('maps rate-limit service unavailability', () => {
    expect(messageFor({ code: 'rate_limit_unavailable' })).toBe(
      'Сервис временно недоступен. Попробуйте позже.'
    );
  });

  it('maps provider capacity with a valid retry delay', () => {
    expect(messageFor({ code: 'provider_temporarily_unavailable', retry_after: 2.1 })).toBe(
      'Сервис анализа временно перегружен. Попробуйте позже. Попробуйте снова через 3 сек.'
    );
  });

  it('uses the safe generic message for an unknown code', () => {
    expect(messageFor({ code: 'internal_provider_failure', detail: 'raw provider response' })).toBe(
      'Произошла ошибка при проверке. Попробуйте позже.'
    );
  });

  it.each([undefined, null, -1, 0, Number.NaN, '17', {}])(
    'ignores malformed retry_after %#',
    (retryAfter) => {
      expect(messageFor({ code: 'provider_temporarily_unavailable', retry_after: retryAfter })).toBe(
        'Сервис анализа временно перегружен. Попробуйте позже.'
      );
    }
  );

  it('never exposes backend detail or internal fields', () => {
    const secret = 'provider=hidden api_key=secret stack=trace';
    const message = messageFor({
      code: 'rate_limit_exceeded',
      detail: secret,
      provider: 'hidden',
      technical: { stack: secret },
    });
    expect(message).toBe('Слишком много запросов. Попробуйте немного позже.');
    expect(message).not.toContain(secret);
    expect(message).not.toContain('provider=');
  });
});
