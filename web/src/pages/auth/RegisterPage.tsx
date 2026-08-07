/**
 * Register Page
 * =============
 * Страница регистрации нового пользователя.
 */

import { useNavigate } from 'react-router-dom';
import { RegisterForm } from '../../components/forms';

export function RegisterPage() {
  const navigate = useNavigate();

  const handleSuccess = () => {
    navigate('/dashboard');
  };

  return (
    <div className="min-h-screen bg-mv-bg flex items-center justify-center p-4">
      
      <div className="relative w-full max-w-md">
        <RegisterForm onSuccess={handleSuccess} />
      </div>
    </div>
  );
}
