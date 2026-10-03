import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowUpRight, ChevronLeft, Clock3, Cpu, Mail, ShieldCheck, UserPlus, Users, X } from 'lucide-react';
import { Card, Button } from '../../components/ui';
import type { ProviderKey } from '../../lib/admin';
import { workspaceApi, WorkspaceApiError, type Workspace } from '../../lib/workspaces';

const PROVIDERS: Array<{ key: ProviderKey; label: string; unit: string }> = [
  { key: 'gemini', label: 'Gemini', unit: 'операций' },
  { key: 'sightengine', label: 'Sightengine', unit: 'проверок' },
  { key: 'aiornot', label: 'AI or Not', unit: 'слов' },
  { key: 'sapling', label: 'Sapling', unit: 'символов' },
  { key: 'resemble', label: 'Resemble', unit: 'проверок' },
];

const formatNumber = (value: number) => value.toLocaleString('ru-RU');

function nextReset() {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1)).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
}

function quotaTone(percentLeft: number) {
  if (percentLeft <= 20) return { line: 'bg-red-500', text: 'text-red-600', label: 'Лимит заканчивается' };
  if (percentLeft <= 50) return { line: 'bg-amber-400', text: 'text-amber-600', label: 'Половина лимита' };
  return { line: 'bg-mv-real', text: 'text-mv-real', label: 'Лимит в норме' };
}

export function WorkspaceDetailsPage() {
  const { workspaceId = '' } = useParams();
  const [workspace, setWorkspace] = useState<Workspace>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteSending, setInviteSending] = useState(false);
  const [inviteNotice, setInviteNotice] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const { workspaces } = await workspaceApi.list();
      const found = workspaces.find((item) => item.workspace_id === workspaceId);
      if (!found) setError('Компания не найдена или у вас нет доступа.'); else setWorkspace(found);
    } catch (cause) { setError(cause instanceof WorkspaceApiError ? cause.message : 'Не удалось загрузить статистику компании.'); }
    finally { setLoading(false); }
  }, [workspaceId]);
  useEffect(() => { void load(); }, [load]);

  const invite = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!workspace || !inviteEmail.trim()) return;
    setInviteSending(true); setInviteNotice('');
    try {
      await workspaceApi.invite(workspace.workspace_id, inviteEmail.trim());
      setInviteEmail(''); setInviteOpen(false);
      setInviteNotice('Приглашение создано. Статус доставки письма вернёт backend после подключения почтового провайдера.');
    } catch (cause) { setInviteNotice(cause instanceof WorkspaceApiError ? cause.message : 'Не удалось создать приглашение.'); }
    finally { setInviteSending(false); }
  };

  const quotas = useMemo(() => workspace ? PROVIDERS.map((provider) => {
    const limit = workspace.provider_quota_overrides[provider.key] ?? 0;
    const reportedUsage = workspace.provider_usage?.[provider.key];
    const used = typeof reportedUsage === 'number' ? Math.min(reportedUsage, limit) : null;
    return { ...provider, limit, used, left: used === null ? null : Math.max(0, limit - used) };
  }) : [], [workspace]);
  const activeModels = quotas.filter((item) => item.limit > 0);
  const usedModels = activeModels.filter((item) => (item.used ?? 0) > 0).length;

  if (loading) return <p className="text-mv-text-secondary">Загрузка статистики…</p>;
  if (!workspace) return <section><Link to="/dashboard/workspaces" className="inline-flex items-center gap-1 text-sm text-mv-text-secondary"><ChevronLeft className="h-4 w-4" />К компаниям</Link><p className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-red-700">{error || 'Компания не найдена.'}</p></section>;

  return <div className="max-w-6xl mx-auto space-y-8 pb-[200px]">
    <Link to="/dashboard/workspaces" className="inline-flex items-center gap-1 text-sm text-mv-text-secondary hover:text-mv-text"><ChevronLeft className="h-4 w-4" />Все компании</Link>
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div><h1 className="text-2xl font-bold text-mv-text">{workspace.name}</h1><p className="mt-1 text-mv-text-secondary">Общий баланс моделей для всей команды</p></div>
      <div className="flex flex-wrap gap-2">{workspace.role === 'owner' && <Button variant="secondary" onClick={() => setInviteOpen(true)} leftIcon={<UserPlus className="h-4 w-4" />}>Добавить сотрудника</Button>}<Link to={`/dashboard/check?workspaceId=${workspace.workspace_id}`}><Button className="text-white" leftIcon={<ArrowUpRight className="h-4 w-4" />}>Новая проверка</Button></Link></div>
    </div>
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Участники" value={`${workspace.member_count + 1} / 10`} subtitle="в компании" icon={<Users className="h-5 w-5 text-mv-accent" />} />
      <StatCard label="Активные модели" value={`${activeModels.length} / ${PROVIDERS.length}`} subtitle="с общим лимитом" icon={<Cpu className="h-5 w-5 text-mv-accent" />} />
      <StatCard label="Используются" value={String(usedModels)} subtitle="модели в этом месяце" icon={<ShieldCheck className="h-5 w-5 text-mv-real" />} green />
      <StatCard label="Следующий сброс" value={nextReset()} subtitle="в 03:00 по Москве" icon={<Clock3 className="h-5 w-5 text-mv-text-secondary" />} />
    </div>
    <section><div className="mb-4"><h2 className="text-xl font-bold text-mv-text">Остаток общих лимитов</h2><p className="mt-1 text-sm text-mv-text-secondary">Расход любого участника уменьшает общий баланс его модели.</p></div><div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">{quotas.map(({ key, ...quota }) => <QuotaCard key={key} {...quota} />)}</div></section>
    {inviteNotice && <p role="status" className="rounded-xl border border-mv-border bg-white px-4 py-3 text-sm text-mv-text-secondary">{inviteNotice}</p>}
    {inviteOpen && <InviteModal email={inviteEmail} sending={inviteSending} onChange={setInviteEmail} onClose={() => !inviteSending && setInviteOpen(false)} onSubmit={invite} />}
  </div>;
}

function InviteModal({ email, sending, onChange, onClose, onSubmit }: { email: string; sending: boolean; onChange: (value: string) => void; onClose: () => void; onSubmit: (event: React.FormEvent) => void }) {
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/25 p-4 backdrop-blur-[2px]" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><form onSubmit={onSubmit} className="w-full max-w-md rounded-[22px] border border-black/[.09] bg-white p-6 shadow-[var(--shadow-card)]"><div className="flex items-start justify-between gap-4"><div><p className="eyebrow">КОМАНДА</p><h2 className="mt-1 text-xl font-semibold text-mv-text">Добавить сотрудника</h2><p className="mt-2 text-sm leading-5 text-mv-text-secondary">Укажите email коллеги. В компании может быть до 10 активных участников.</p></div><button type="button" onClick={onClose} aria-label="Закрыть" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-black/[.09]"><X className="h-4 w-4" /></button></div><label className="mt-6 block text-sm font-medium text-mv-text">Электронная почта<input required type="email" autoFocus value={email} onChange={(event) => onChange(event.target.value)} placeholder="colleague@company.ru" className="mt-2 w-full rounded-xl border border-mv-border px-3 py-3 outline-none focus:border-black" /></label><p className="mt-3 flex gap-2 text-xs leading-5 text-mv-text-muted"><Mail className="mt-0.5 h-3.5 w-3.5 shrink-0" />Письмо отправит backend после подключения почтового провайдера.</p><div className="mt-6 flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onClose}>Отмена</Button><Button type="submit" disabled={sending} className="text-white" leftIcon={<Mail className="h-4 w-4" />}>{sending ? 'Отправляем…' : 'Отправить приглашение'}</Button></div></form></div>;
}

function StatCard({ label, value, subtitle, icon, green = false }: { label: string; value: string; subtitle: string; icon: React.ReactNode; green?: boolean }) {
  return <Card className="relative"><div><p className="text-sm text-mv-text-secondary">{label}</p><p className={`mt-1 text-2xl font-bold ${green ? 'text-mv-real' : 'text-mv-text'}`}>{value}</p></div><div className="absolute right-[5px] top-[5px] flex h-10 w-10 items-center justify-center rounded-lg bg-mv-accent/10">{icon}</div><p className="mt-4 text-sm text-mv-text-muted">{subtitle}</p></Card>;
}

function QuotaCard({ label, unit, limit, used, left }: { label: string; unit: string; limit: number; used: number | null; left: number | null }) {
  if (!limit) return <Card><p className="font-semibold text-mv-text">{label}</p><p className="mt-1 text-sm text-mv-text-secondary">{unit} в месяц</p><p className="mt-7 text-2xl font-bold text-mv-text-muted">Лимит не выдан</p></Card>;
  if (used === null || left === null) return <Card><p className="font-semibold text-mv-text">{label}</p><p className="mt-1 text-sm text-mv-text-secondary">{unit} в месяц</p><p className="mt-6 text-3xl font-bold text-mv-text">{formatNumber(limit)}</p><p className="mt-1 text-sm text-mv-text-secondary">Общий лимит</p><p className="mt-4 text-sm text-mv-text-muted">Расход загрузит backend.</p></Card>;
  const percentLeft = Math.round((left / limit) * 100);
  const tone = quotaTone(percentLeft);
  return <Card className="relative overflow-hidden"><div className="flex items-start justify-between"><div><p className="font-semibold text-mv-text">{label}</p><p className="mt-1 text-sm text-mv-text-secondary">{unit} в месяц</p></div><div className="h-2.5 w-2.5 rounded-full bg-mv-accent" /></div><p className="mt-6 text-3xl font-bold text-mv-text">{formatNumber(left)} <span className="text-base font-medium text-mv-text-muted">осталось</span></p><p className="mt-1 text-sm text-mv-text-secondary">Использовано {formatNumber(used)} из {formatNumber(limit)}</p><div className="mt-4 h-2 overflow-hidden rounded-full bg-mv-surface-2"><div className={`h-full rounded-full ${tone.line}`} style={{ width: `${percentLeft}%` }} /></div><p className={`mt-3 text-sm font-medium ${tone.text}`}>{tone.label} · {percentLeft}%</p></Card>;
}
