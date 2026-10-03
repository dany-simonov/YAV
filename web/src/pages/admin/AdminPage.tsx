import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, Database, Eye, LogOut, RefreshCw, Search, ShieldCheck, SlidersHorizontal, Users } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { adminApi, type AdminUserDetails, type AdminUserSummary, type ProviderBudgetUsage, type ProviderKey } from '../../lib/admin';
import { useAuthStore } from '../../store';
import { UserDetailsModal } from './UserDetailsModal';
import { ProviderUsageChart } from './ProviderUsageChart';

export function AdminPage() {
  const { logout, user } = useAuthStore();
  const navigate = useNavigate();
  const [users, setUsers] = useState<AdminUserSummary[]>([]);
  const [details, setDetails] = useState<AdminUserDetails | null>(null);
  const [activeUserId, setActiveUserId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [providerBudgets, setProviderBudgets] = useState<Record<string, ProviderBudgetUsage> | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const totals = useMemo(() => ({ total: users.length, overrides: users.filter((item) => item.has_provider_quota_overrides).length }), [users]);

  async function loadDetails(id: string, showLoading = true) {
    if (showLoading) setDetailsLoading(true);
    setDetailsError(null);
    try { setDetails(await adminApi.getUser(id)); }
    catch (error) { setDetailsError(error instanceof Error ? error.message : 'Не удалось загрузить профиль'); }
    finally { if (showLoading) setDetailsLoading(false); }
  }
  async function loadUsers(query = '', refreshOpen = false) {
    setLoading(true); setMessage(null);
    try {
      const result = await adminApi.listUsers({ pageSize: 50, search: query.trim() || undefined });
      setUsers(result.users);
      // Provider budgets belong to the project. The existing admin response
      // exposes the same global counters with any user card, so load one row
      // once and keep the counters above the whole table.
      if (result.users[0]) {
        const overview = await adminApi.getUser(result.users[0].user_id);
        setProviderBudgets(overview.usage.provider_budgets.quotas);
      } else setProviderBudgets(null);
      if (refreshOpen && activeUserId) await loadDetails(activeUserId, false);
    }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Не удалось загрузить пользователей'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void loadUsers(); }, []);
  function openUser(id: string, opener: HTMLElement) { openerRef.current = opener; setActiveUserId(id); setDetails(null); setDetailsError(null); void loadDetails(id); }
  function closeModal() { setActiveUserId(null); setDetails(null); setDetailsError(null); requestAnimationFrame(() => openerRef.current?.focus()); }
  async function setProviderLimit(key: ProviderKey, value: number) { if (!details) return; setSaving(true); setMessage(null); try { const overrides = Object.fromEntries(Object.entries(details.usage.provider_quotas).map(([provider, quota]) => [provider, quota?.limit])); await adminApi.setProviderOverrides(details.user_id, { ...overrides, [key]: value }); await loadDetails(details.user_id, false); await loadUsers(search); setMessage(`Лимит ${key} выдан.`); } catch (error) { setMessage(error instanceof Error ? error.message : 'Не удалось выдать лимит нейросети'); } finally { setSaving(false); } }
  async function exit() { await logout(); navigate('/admin/login', { replace: true }); }

  return <main className="min-h-screen bg-[#f6f6f4] text-mv-text">
    <header className="border-b border-black/10 bg-white"><div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4"><div className="flex items-center gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-black text-white"><ShieldCheck className="h-5 w-5" /></span><div><p className="text-xs font-semibold tracking-[.14em] text-mv-text-muted">ЯВЬ · ADMIN</p><h1 className="font-semibold">Управление лимитами</h1></div></div><button onClick={exit} className="inline-flex items-center gap-2 text-sm text-mv-text-secondary hover:text-black"><LogOut className="h-4 w-4" />Выйти</button></div></header>
    <div className="mx-auto max-w-7xl px-5 py-8"><div className="grid gap-4 sm:grid-cols-2"><Metric icon={<Users />} label="Free-аккаунты в выдаче" value={totals.total} /><Metric icon={<SlidersHorizontal />} label="Аккаунты с выданным лимитом" value={totals.overrides} /></div><GlobalBudgets quotas={providerBudgets} />{message && <p role="status" className="mt-5 rounded-xl border border-black/10 bg-white px-4 py-3 text-sm">{message}</p>}
      <section className="mt-6 rounded-2xl border border-black/10 bg-white p-4 sm:p-5"><div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="font-semibold">Пользователи</h2><p className="mt-1 text-sm text-mv-text-secondary">Поиск по точному email или ID Appwrite. Нажмите на строку для просмотра.</p></div><button onClick={() => void loadUsers(search, true)} className="inline-flex items-center gap-2 text-sm"><RefreshCw className="h-4 w-4" />Обновить</button></div><form onSubmit={(event) => { event.preventDefault(); void loadUsers(search, true); }} className="mt-4 flex gap-2"><input value={search} onChange={(event) => setSearch(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-black/10 px-3 py-2.5 text-sm" placeholder="Email или ID" /><button className="rounded-lg bg-black px-3 text-white" aria-label="Найти"><Search className="h-4 w-4" /></button></form>
        <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[540px] text-left text-sm"><thead className="border-b border-black/10 text-xs text-mv-text-muted"><tr><th className="pb-3 font-medium">Пользователь</th><th className="pb-3 font-medium">Email</th><th className="pb-3 font-medium">Статус лимитов</th></tr></thead><tbody>{loading ? <tr><td colSpan={3} className="py-10 text-center text-mv-text-secondary">Загрузка…</td></tr> : users.length ? users.map((item) => <tr tabIndex={0} onClick={(event) => openUser(item.user_id, event.currentTarget)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openUser(item.user_id, event.currentTarget); } }} className="cursor-pointer border-b border-black/[.06] outline-none hover:bg-black/[.02] focus-visible:bg-black/[.04]" key={item.user_id}><td className="py-4"><p className="font-medium">{item.display_name || 'Без имени'}</p><p className="mt-0.5 max-w-[140px] truncate text-xs text-mv-text-muted">{item.user_id}</p></td><td className="py-4 text-mv-text-secondary">{item.email || '—'}</td><td className="py-4 text-mv-text-secondary">{item.has_provider_quota_overrides ? 'Выдан индивидуально' : 'Лимит не выдан'}</td></tr>) : <tr><td colSpan={3} className="py-10 text-center text-mv-text-secondary">Пользователи не найдены.</td></tr>}</tbody></table></div></section><ProviderUsageChart />
      <p className="mt-6 text-xs text-mv-text-muted">Вы вошли как {user?.email}. Все изменения тарифов и квот фиксируются серверным аудитом.</p>
    </div>
    <UserDetailsModal open={activeUserId !== null} user={details} loading={detailsLoading} error={detailsError} saving={saving} status={message} onClose={closeModal} onRetry={() => activeUserId && void loadDetails(activeUserId)} onProviderLimit={setProviderLimit} />
  </main>;
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) { return <article className="rounded-2xl border border-black/10 bg-white p-5"><div className="flex items-center gap-2 text-mv-text-secondary"><span className="[&_svg]:h-4 [&_svg]:w-4">{icon}</span><span className="text-sm">{label}</span></div><p className="mt-3 text-3xl font-semibold tracking-[-.04em]">{value}</p></article>; }

const budgetCards = [
  ['gemini_operations', 'Gemini', Activity], ['sightengine_monthly', 'Sightengine', Eye], ['aiornot_words_monthly', 'AI or Not', ShieldCheck], ['sapling_chars_monthly', 'Sapling', Database], ['resemble_monthly', 'Resemble', Activity],
] as const;
function budgetColor(quota: ProviderBudgetUsage) { const remainingRatio = Math.max(0, quota.remaining) / quota.limit; return remainingRatio > .5 ? 'bg-mv-real text-mv-real' : remainingRatio >= .2 ? 'bg-mv-uncertain text-mv-uncertain' : 'bg-mv-fake text-mv-fake'; }
function GlobalBudgets({ quotas }: { quotas: Record<string, ProviderBudgetUsage> | null }) {
  return <section className="mt-6"><div className="mb-3"><h2 className="font-semibold">Общие бюджеты API</h2><p className="mt-1 text-sm text-mv-text-secondary">Расход всех аккаунтов проекта по каждой модели.</p></div><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">{budgetCards.map(([key, label, Icon]) => { const quota = quotas?.[key]; const color = quota ? budgetColor(quota) : ''; return <article key={key} className="rounded-2xl border border-black/10 bg-white p-4"><div className="flex items-center justify-between"><span className="text-sm font-medium">{label}</span><Icon className="h-4 w-4 text-mv-text-secondary" /></div>{quota ? <><p className="mt-4 text-2xl font-semibold tabular-nums">{quota.used.toLocaleString('ru-RU')} <span className="text-sm font-normal text-mv-text-muted">/ {quota.limit.toLocaleString('ru-RU')}</span></p><div className="mt-3 h-2 overflow-hidden rounded-full bg-black/[.08]"><div className={`h-full rounded-full ${color.split(' ')[0]}`} style={{ width: `${Math.min(100, quota.used / quota.limit * 100)}%` }} /></div><p className={`mt-2 text-xs ${color.split(' ')[1]}`}>Осталось {quota.remaining.toLocaleString('ru-RU')}</p></> : <p className="mt-4 text-sm text-mv-text-muted">Данные ещё не вернул backend.</p>}</article>; })}</div></section>;
}
