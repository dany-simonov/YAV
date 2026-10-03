import { APPWRITE_CONFIG, functions } from './appwrite';
import type { ProviderKey } from './admin';

export type WorkspaceRole = 'owner' | 'member';

export interface Workspace {
  workspace_id: string;
  name: string;
  owner_user_id: string;
  member_count: number;
  role: WorkspaceRole;
  provider_quota_overrides: Partial<Record<ProviderKey, number>>;
  /** The backend returns spent provider units for the current month. */
  provider_usage?: Partial<Record<ProviderKey, number>>;
}

export interface WorkspaceMember { user_id: string; role: WorkspaceRole; status: string; created_at: string }
export interface WorkspaceInvitation { invitation_id: string; workspace_id: string; email: string; status: string; expires_at: string }

export class WorkspaceApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code?: string) {
    super(message); this.name = 'WorkspaceApiError';
  }
}

async function call<T>(body: Record<string, unknown>): Promise<T> {
  const execution = await functions.createExecution({
    functionId: APPWRITE_CONFIG.functions.analyze,
    body: JSON.stringify(body),
  });
  let payload: { detail?: string; code?: string } = {};
  try { payload = JSON.parse(execution.responseBody || '{}'); } catch { /* handled below */ }
  if (execution.responseStatusCode >= 400) {
    throw new WorkspaceApiError(payload.detail || 'Не удалось выполнить действие команды.', execution.responseStatusCode, payload.code);
  }
  return payload as T;
}

export const workspaceApi = {
  list: () => call<{ workspaces: Workspace[] }>({ action: 'workspace_get', pageSize: 100 }),
  create: (name: string) => call<Workspace>({ action: 'workspace_create', name }),
  members: (workspaceId: string) => call<{ members: WorkspaceMember[] }>({ action: 'workspace_list_members', workspaceId }),
  invitations: (workspaceId: string) => call<{ invitations: WorkspaceInvitation[] }>({ action: 'workspace_list_invitations', workspaceId }),
  myInvitations: () => call<{ invitations: WorkspaceInvitation[] }>({ action: 'workspace_list_my_invitations', pageSize: 100 }),
  invite: (workspaceId: string, email: string) => call<WorkspaceInvitation>({ action: 'workspace_invite_member', workspaceId, email }),
  accept: (workspaceId: string) => call({ action: 'workspace_accept_invitation', workspaceId }),
  reject: (workspaceId: string) => call({ action: 'workspace_reject_invitation', workspaceId }),
  setProviderLimits: (workspaceId: string, overrides: Partial<Record<ProviderKey, number>>) =>
    call<{ provider_quota_overrides: Partial<Record<ProviderKey, number>> }>({ action: 'workspace_set_provider_quota_overrides', workspaceId, overrides }),
};
