/**
 * Protected Route Component
 * =========================
 * HOC для защиты маршрутов от неавторизованных пользователей.
 */

import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../../store';
import { Spinner } from '../ui';

interface ProtectedRouteProps {
  children: React.ReactNode;
  redirectTo?: string;
}

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

  // Redirect to login if not authenticated
  if (!user) {
    return <Navigate to={redirectTo} state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

/** Requires both an active account and a confirmed email address. */
export function VerifiedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading, isInitialized } = useAuthStore();
  const location = useLocation();

  if (!isInitialized || isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-mv-bg">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (!user.emailVerification) {
    return <Navigate to="/verify-email/pending" state={{ from: location }} replace />;
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

  // Redirect to dashboard if already authenticated
  if (user) {
    // Check if there's a return path
    const from = (location.state as { from?: Location })?.from?.pathname || redirectTo;
    return <Navigate to={from} replace />;
  }

  return <>{children}</>;
}
