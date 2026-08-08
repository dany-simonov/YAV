import { functions, APPWRITE_CONFIG } from './appwrite';

/** Ensure the authenticated account has its server-managed users row. */
export async function ensureUserProfile(): Promise<string> {
  const execution = await functions.createExecution({
    functionId: APPWRITE_CONFIG.functions.analyze,
    body: JSON.stringify({ action: 'ensure_profile' }),
  });

  if (!execution.responseBody) {
    throw new Error('Функция профиля не вернула ответ');
  }

  const response = JSON.parse(execution.responseBody) as {
    profile_id?: string;
    detail?: string;
  };
  if (execution.responseStatusCode >= 400 || response.detail || !response.profile_id) {
    throw new Error(response.detail || 'Не удалось создать профиль пользователя');
  }
  return response.profile_id;
}
