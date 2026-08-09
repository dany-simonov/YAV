/** Safe, centralized presentation of structured Appwrite Function errors. */

export interface AnalysisBackendError {
  code: string;
  retryAfter?: number;
}

const GENERIC_MESSAGE = 'Произошла ошибка при проверке. Попробуйте позже.';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validRetryAfter(value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return undefined;
  return Math.ceil(value);
}

/** Parse only the small public error contract; ignore all other response fields. */
export function parseAnalysisBackendError(value: unknown): AnalysisBackendError | null {
  if (!isRecord(value) || typeof value.code !== 'string' || !value.code) return null;
  return { code: value.code, retryAfter: validRetryAfter(value.retry_after) };
}

function withRetry(base: string, retryAfter: number | undefined): string {
  return retryAfter ? `${base} Попробуйте снова через ${retryAfter} сек.` : base;
}

/** Return an allowlisted user-facing message; never render backend detail or raw errors. */
export function analysisErrorMessage(error: AnalysisBackendError | null): string {
  if (!error) return GENERIC_MESSAGE;

  switch (error.code) {
    case 'rate_limit_exceeded':
      return error.retryAfter
        ? `Слишком много запросов. Попробуйте снова через ${error.retryAfter} сек.`
        : 'Слишком много запросов. Попробуйте немного позже.';
    case 'daily_quota_exceeded':
      return withRetry('Дневной лимит проверок исчерпан.', error.retryAfter);
    case 'monthly_quota_exceeded':
      return 'Месячный лимит проверок исчерпан.';
    case 'rate_limit_unavailable':
      return 'Сервис временно недоступен. Попробуйте позже.';
    case 'provider_temporarily_unavailable':
      return withRetry('Сервис анализа временно перегружен. Попробуйте позже.', error.retryAfter);
    default:
      return GENERIC_MESSAGE;
  }
}

/** Internal control-flow error whose message deliberately contains no backend data. */
export class AnalysisExecutionError extends Error {
  constructor(public readonly backendError: AnalysisBackendError | null) {
    super('analysis_execution_failed');
  }
}

export function analysisErrorMessageFromUnknown(error: unknown): string {
  return error instanceof AnalysisExecutionError
    ? analysisErrorMessage(error.backendError)
    : GENERIC_MESSAGE;
}
