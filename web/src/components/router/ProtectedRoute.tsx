/**
 * Protected Route Component
 * =========================
 * HOC для защиты маршрутов от неавторизованных пользователей.
 */

import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore, type User } from '../../store';
import { Spinner } from '../ui';

interface ProtectedRouteProps {
  children: React.ReactNode;
  redirectTo?: string;
}

export const getProtectedRouteRedirect = (user: User | null): string | null => {
  if (!user) return '/login';
  return user.emailVerification ? null : '/verify-email';
};

export const getPublicOnlyRedirect = (
  user: User | null,
  verifiedRedirect = '/dashboard'
): string | null => {
  if (!user) return null;
  return user.emailVerification ? verifiedRedirect : '/verify-email';
};

export const getVerificationRouteRedirect = (user: User | null): string | null => {
  if (!user) return '/login';
  return user.emailVerification ? '/dashboard' : null;
};

export function ProtectedRoute({ 
  children, 
  redirectTo = '/login' 
}: ProtectedRouteProps) {
  const { user, isLoading, isInitialized } = useAuthStore();
  const location = useLocation();

  // Show loading spinner while checking auth
  if (!isInitialized || isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-mv-bg">
        <div className="text-center">
          <Spinner size="lg" className="mx-auto mb-4" />
          <p className="text-mv-text-secondary">Проверка авторизации...</p>
        </div>
      </div>
    );
  }

  const redirect = getProtectedRouteRedirect(user);
  if (redirect) {
    return (
      <Navigate
        to={redirect === '/login' ? redirectTo : redirect}
        state={{ from: location }}
        replace
      />
    );
  }

  return <>{children}</>;
}

/**
 * Public Only Route Component
 * ===========================
 * Редиректит авторизованных пользователей (например, со страницы логина).
 */
interface PublicOnlyRouteProps {
  children: React.ReactNode;
  redirectTo?: string;
}

export function PublicOnlyRoute({ 
  children, 
  redirectTo = '/dashboard' 
}: PublicOnlyRouteProps) {
  const { user, isLoading, isInitialized } = useAuthStore();
  const location = useLocation();

  // Show loading spinner while checking auth
  if (!isInitialized || isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-mv-bg">
        <div className="text-center">
          <Spinner size="lg" className="mx-auto mb-4" />
          <p className="text-mv-text-secondary">Загрузка...</p>
        </div>
      </div>
    );
  }

  const redirect = getPublicOnlyRedirect(user, redirectTo);
  if (redirect) {
    // Check if there's a return path
    const from = (location.state as { from?: Location })?.from?.pathname;
    return <Navigate to={user?.emailVerification && from ? from : redirect} replace />;
  }

  return <>{children}</>;
}

export function VerificationRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading, isInitialized } = useAuthStore();

  if (!isInitialized || isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-mv-bg">
        <Spinner size="lg" />
      </div>
    );
  }

  const redirect = getVerificationRouteRedirect(user);
  return redirect ? <Navigate to={redirect} replace /> : <>{children}</>;
}
