import { functions, APPWRITE_CONFIG } from './appwrite';

export type Subscription = 'free' | 'pro' | 'enterprise' | 'custom';
export type QuotaKey = 'checks' | 'heavy_media_checks';

export interface AdminUserSummary {
  user_id: string;
  email: string;
  display_name: string;
  email_verified: boolean;
  subscription: Subscription;
  has_quota_overrides: boolean;
  has_provider_quota_overrides: boolean;
  created_at: string;
  updated_at: string;
}

export interface QuotaUsage {
  limit: number;
  used: number;
  remaining: number;
  window: string;
  reset_at: string;
  generation: number;
}

export interface ModelUsage {
  provider: string;
  model: string;
  used: number;
}

export interface ModelUsageMonth {
  scope: 'completed_checks';
  period: 'month';
  window: string;
  reset_at: string;
  total: number;
  truncated: boolean;
  models: ModelUsage[];
}

export interface ProviderBudgetUsage extends Omit<QuotaUsage, 'generation'> {
  dimension: string;
}

export type ProviderQuotaUsage = Omit<QuotaUsage, 'generation'>;
export type ProviderKey = 'gemini' | 'sightengine' | 'aiornot' | 'sapling' | 'resemble';
export interface ProviderUsageHistory { provider: ProviderKey; unit: string; daily_limit: number; points: Array<{ date: string; used: number }>; }

export interface AdminUserDetails {
  user_id: string;
  subscription: Subscription;
  overrides: Partial<Record<QuotaKey, number>>;
  effective_limits: Record<QuotaKey, { period: 'day' | 'month'; limit: number }>;
  user: { user_id: string; email: string; display_name: string; email_verified: boolean };
  usage: {
    user_quotas: Record<QuotaKey, QuotaUsage>;
    provider_quotas: Partial<Record<ProviderKey, ProviderQuotaUsage>>;
    model_checks_month: ModelUsageMonth;
    provider_budgets: { scope: 'global'; attributable_to_target_user: false; quotas: Record<string, ProviderBudgetUsage> };
  };
}

type FunctionError = { detail?: string; code?: string };

export class AdminApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code?: string) {
    super(message);
    this.name = 'AdminApiError';
  }
}

async function adminCall<T>(body: Record<string, unknown>): Promise<T> {
  const execution = await functions.createExecution({
    functionId: APPWRITE_CONFIG.functions.analyze,
    body: JSON.stringify(body),
  });
  let payload: T & FunctionError = {} as T & FunctionError;
  try { payload = JSON.parse(execution.responseBody || '{}') as T & FunctionError; } catch { /* safe generic error below */ }
  if (execution.responseStatusCode >= 400) {
    throw new AdminApiError(
      payload.detail || 'Не удалось выполнить действие администратора',
      execution.responseStatusCode,
      payload.code,
    );
  }
  return payload as T;
}

export const adminApi = {
  listUsers: (params: { pageSize?: number; cursor?: string; search?: string } = {}) => adminCall<{ users: AdminUserSummary[]; next_cursor: string | null }>({ action: 'admin_list_users', ...params }),
  getUser: (targetUserId: string) => adminCall<AdminUserDetails>({ action: 'admin_get_user_policy', targetUserId }),
  setSubscription: (targetUserId: string, subscription: Subscription) => adminCall({ action: 'admin_set_subscription', targetUserId, subscription }),
  setOverrides: (targetUserId: string, overrides: Partial<Record<QuotaKey, number>>) => adminCall({ action: 'admin_set_quota_overrides', targetUserId, overrides }),
  setProviderOverrides: (targetUserId: string, overrides: Partial<Record<ProviderKey, number>>) => adminCall({ action: 'admin_set_provider_quota_overrides', targetUserId, overrides }),
  getProviderUsageHistory: (provider: ProviderKey, days: 7 | 30 | 90): Promise<ProviderUsageHistory> => adminCall<ProviderUsageHistory>({ action: 'admin_provider_usage_history', provider, days }),
  resetUsage: (targetUserId: string, quotaKey: QuotaKey) => adminCall({ action: 'admin_reset_user_quota_usage', targetUserId, quotaKey, idempotencyKey: crypto.randomUUID() }),
};
