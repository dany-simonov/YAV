/**
 * Register Page
 * =============
 * Страница регистрации нового пользователя.
 */

import { useNavigate } from 'react-router-dom';
import { RegisterForm } from '../../components/forms';
import type { AuthActionResult } from '../../store';

export function RegisterPage() {
  const navigate = useNavigate();

  const handleSuccess = (result: AuthActionResult) => {
    if (result.user?.emailVerification) {
      navigate('/dashboard', { replace: true });
      return;
    }
    navigate('/verify-email', {
      replace: true,
      state: {
        verificationEmailSent: result.verificationEmailSent,
        verificationError: result.error,
      },
    });
  };

  return (
    <div className="min-h-screen bg-mv-bg flex items-center justify-center p-4">
      
      <div className="relative w-full max-w-md">
        <RegisterForm onSuccess={handleSuccess} />
      </div>
    </div>
  );
}
