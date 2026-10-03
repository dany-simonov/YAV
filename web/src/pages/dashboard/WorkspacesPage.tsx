import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Building2, Plus, Users } from 'lucide-react';
import type { ProviderKey } from '../../lib/admin';
import { workspaceApi, WorkspaceApiError, type Workspace, type WorkspaceInvitation } from '../../lib/workspaces';

const PROVIDERS: Array<{ key: ProviderKey; label: string; unit: string }> = [
  { key: 'gemini', label: 'Gemini', unit: 'операций' },
  { key: 'sightengine', label: 'Sightengine', unit: 'проверок' },
  { key: 'aiornot', label: 'AI or Not', unit: 'слов' },
  { key: 'sapling', label: 'Sapling', unit: 'символов' },
  { key: 'resemble', label: 'Resemble', unit: 'проверок' },
];

export function WorkspacesPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [spaces, invites] = await Promise.all([workspaceApi.list(), workspaceApi.myInvitations()]);
      setWorkspaces(spaces.workspaces); setInvitations(invites.invitations);
    } catch (cause) {
      setError(cause instanceof WorkspaceApiError ? cause.message : 'Не удалось загрузить команды.');
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault(); if (!name.trim()) return;
    setBusy(true); setError('');
    try { await workspaceApi.create(name.trim()); setName(''); await load(); }
    catch (cause) { setError(cause instanceof WorkspaceApiError ? cause.message : 'Не удалось создать команду.'); }
    finally { setBusy(false); }
  };
  const respond = async (invite: WorkspaceInvitation, accept: boolean) => {
    setBusy(true); setError('');
    try { accept ? await workspaceApi.accept(invite.workspace_id) : await workspaceApi.reject(invite.workspace_id); await load(); }
    catch (cause) { setError(cause instanceof WorkspaceApiError ? cause.message : 'Не удалось обработать приглашение.'); }
    finally { setBusy(false); }
  };

  return <section className="space-y-6">
    <div><p className="text-sm font-semibold text-mv-accent">КОМАНДЫ</p><h1 className="mt-1 text-3xl font-bold tracking-tight">Общие лимиты команды</h1><p className="mt-2 text-mv-text-secondary">Каждая проверка в команде списывается из одного лимита модели. В команде — до 10 человек.</p></div>
    {error && <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
    <form onSubmit={create} className="flex flex-col gap-3 rounded-2xl border border-black/10 bg-white p-5 shadow-sm sm:flex-row">
      <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="Название команды, например «Редакция ЯВЬ»" className="min-w-0 flex-1 rounded-xl border border-black/10 px-4 py-3 outline-none focus:border-black" />
      <button disabled={busy} className="inline-flex items-center justify-center gap-2 rounded-xl bg-black px-5 py-3 font-semibold text-white disabled:opacity-50"><Plus className="h-4 w-4" />Создать команду</button>
    </form>
    {invitations.length > 0 && <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5"><h2 className="font-bold">Приглашения в команды</h2><div className="mt-3 space-y-2">{invitations.map((invite) => <div key={invite.invitation_id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-white p-3"><span>Приглашение в команду · действует до {new Date(invite.expires_at).toLocaleDateString('ru-RU')}</span><span className="flex gap-2"><button disabled={busy} onClick={() => void respond(invite, true)} className="rounded-lg bg-black px-3 py-2 text-sm font-semibold text-white">Принять</button><button disabled={busy} onClick={() => void respond(invite, false)} className="rounded-lg border border-black/15 px-3 py-2 text-sm">Отклонить</button></span></div>)}</div></div>}
    {loading ? <p className="text-mv-text-secondary">Загрузка команд…</p> : workspaces.length === 0 ? <div className="rounded-2xl border border-dashed border-black/20 p-10 text-center text-mv-text-secondary"><Building2 className="mx-auto mb-3 h-8 w-8" />Создайте команду, чтобы выдать общий лимит и пригласить коллег.</div> : <div className="grid gap-4 md:grid-cols-2">{workspaces.map((space) => <WorkspaceCard key={space.workspace_id} workspace={space} />)}</div>}
  </section>;
}

function WorkspaceCard({ workspace }: { workspace: Workspace }) {
  const setProviders = useMemo(() => PROVIDERS.filter(({ key }) => workspace.provider_quota_overrides[key]), [workspace]);
  return <Link to={`/dashboard/workspaces/${workspace.workspace_id}`} className="block rounded-2xl border border-black/10 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"><div className="flex items-start justify-between gap-3"><div><h2 className="text-xl font-bold">{workspace.name}</h2><p className="mt-1 flex items-center gap-1 text-sm text-mv-text-secondary"><Users className="h-4 w-4" />{workspace.member_count + 1} / 10 участников</p></div><span className="rounded-full bg-black/5 px-2.5 py-1 text-xs font-semibold">{workspace.role === 'owner' ? 'Владелец' : 'Участник'}</span></div><div className="mt-5 border-t border-black/5 pt-4 text-sm"><p className="font-medium">Общие лимиты моделей</p><p className="mt-1 text-mv-text-secondary">{setProviders.length ? setProviders.map(({ label, key }) => `${label}: ${workspace.provider_quota_overrides[key]}`).join(' · ') : 'Пока не выданы владельцем'}</p></div></Link>;
}
