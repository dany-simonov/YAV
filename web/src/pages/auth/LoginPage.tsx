/**
 * Login Page
 * ==========
 * Страница авторизации пользователя.
 */

import { useNavigate } from 'react-router-dom';
import { LoginForm } from '../../components/forms';

export function LoginPage() {
  const navigate = useNavigate();

  const handleSuccess = () => {
    navigate('/dashboard');
  };

  return (
    <div className="min-h-screen bg-mv-bg flex items-center justify-center p-4">
      <div className="fixed inset-0 grid-texture opacity-60 pointer-events-none" />
      
      <div className="relative w-full max-w-md">
        <LoginForm onSuccess={handleSuccess} />
      </div>
    </div>
  );
}
