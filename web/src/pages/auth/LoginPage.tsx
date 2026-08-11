/**
 * Login Page
 * ==========
 * Страница авторизации пользователя.
 */

import { useNavigate } from 'react-router-dom';
import { LoginForm } from '../../components/forms';
import { AuthLayout } from '../../components/layout';

export function LoginPage() {
  const navigate = useNavigate();

  const handleSuccess = () => {
    navigate('/dashboard');
  };

  return (
    <AuthLayout mode="login"><LoginForm onSuccess={handleSuccess} /></AuthLayout>
  );
}
