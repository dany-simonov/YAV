/**
 * Login Page
 * ==========
 * Страница авторизации пользователя.
 */

import { useNavigate } from 'react-router-dom';
import { LoginForm } from '../../components/forms';
import type { AuthActionResult } from '../../store';

export function LoginPage() {
  const navigate = useNavigate();

  const handleSuccess = (result: AuthActionResult) => {
    navigate(result.user?.emailVerification ? '/dashboard' : '/verify-email', { replace: true });
  };

  return (
    <div className="min-h-screen bg-mv-bg flex items-center justify-center p-4">
      
      <div className="relative w-full max-w-md">
        <LoginForm onSuccess={handleSuccess} />
      </div>
    </div>
  );
}
