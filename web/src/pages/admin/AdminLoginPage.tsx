import { useState, type FormEvent } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { LockKeyhole, ShieldCheck } from 'lucide-react';
import { useAuthStore } from '../../store';
import { adminApi } from '../../lib/admin';

export function AdminLoginPage() {
  const { user, login, logout, isActionLoading } = useAuthStore();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

  if (user) return <Navigate to="/admin" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const result = await login(email.trim(), password);
    if (!result.success) { setError(result.error || 'Не удалось войти'); return; }
    try {
      await adminApi.listUsers({ pageSize: 1 });
      navigate('/admin', { replace: true });
    } catch {
      await logout();
      setError('Эта учётная запись не имеет доступа к панели администратора.');
    }
  }

  return <main className="min-h-screen bg-[#111] px-5 py-10 flex items-center justify-center">
    <section className="w-full max-w-md rounded-2xl bg-[#fafaf9] p-7 sm:p-9 shadow-2xl">
      <div className="w-11 h-11 rounded-xl bg-black text-white flex items-center justify-center"><ShieldCheck className="w-5 h-5" /></div>
      <p className="mt-6 text-xs font-semibold tracking-[.14em] text-mv-text-muted">ЯВЬ · ADMIN</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-[-.045em]">Панель управления</h1>
      <p className="mt-3 text-sm leading-6 text-mv-text-secondary">Войдите с учётной записью, добавленной в серверный список администраторов.</p>
      {error && <p role="alert" className="mt-5 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
      <form onSubmit={submit} className="mt-6 space-y-4">
        <label className="block text-sm font-medium">Email<input required type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1.5 w-full rounded-lg border border-black/10 bg-white px-3.5 py-3 outline-none focus:border-black" placeholder="admin@yav.ru" /></label>
        <label className="block text-sm font-medium">Пароль<input required minLength={8} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1.5 w-full rounded-lg border border-black/10 bg-white px-3.5 py-3 outline-none focus:border-black" placeholder="••••••••" /></label>
        <button disabled={isActionLoading} className="mt-2 w-full rounded-lg bg-black px-4 py-3 text-sm font-semibold text-white disabled:opacity-60">{isActionLoading ? 'Проверяем…' : <span className="inline-flex items-center gap-2"><LockKeyhole className="w-4 h-4" />Войти в админку</span>}</button>
      </form>
    </section>
  </main>;
}
