import { useEffect, useState, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { AdminApiError, adminApi } from '../../lib/admin';
import { useAuthStore } from '../../store';
import { Spinner } from '../ui';

/**
 * Hides every admin screen until the server has confirmed administrator access.
 * The Function remains the authority; this guard only prevents a normal user
 * from briefly seeing the administration UI through a direct URL.
 */
export function AdminRoute({ children }: { children: ReactNode }) {
  const { user, isLoading, isInitialized } = useAuthStore();
  const location = useLocation();
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!user?.$id) { setAllowed(false); setFailure(null); return undefined; }
    setAllowed(null); setFailure(null);
    adminApi.listUsers({ pageSize: 1 })
      .then(() => { if (!cancelled) setAllowed(true); })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof AdminApiError && error.status === 403 && error.code === 'admin_access_denied') {
          setAllowed(false);
          return;
        }
        setFailure(error instanceof Error ? error.message : 'Не удалось проверить доступ к админке.');
        setAllowed(false);
      });
    return () => { cancelled = true; };
  }, [user?.$id]);

  if (!isInitialized || isLoading || allowed === null) {
    return <div className="min-h-screen flex items-center justify-center bg-mv-bg"><Spinner size="lg" /></div>;
  }
  if (!user) return <Navigate to="/admin/login" state={{ from: location }} replace />;
  if (failure) {
    return <main className="min-h-screen bg-mv-bg flex items-center justify-center p-5"><section className="max-w-lg rounded-2xl border border-red-200 bg-white p-7 shadow-sm"><p className="text-xs font-semibold tracking-[.12em] text-red-600">АДМИНКА НЕДОСТУПНА</p><h1 className="mt-2 text-xl font-semibold">Сервер не подтвердил запрос</h1><p className="mt-3 text-sm leading-6 text-mv-text-secondary">{failure}</p><p className="mt-4 text-xs leading-5 text-mv-text-muted">Права не изменены. Проверьте последнюю execution Function analyze.</p></section></main>;
  }
  if (!allowed) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}
