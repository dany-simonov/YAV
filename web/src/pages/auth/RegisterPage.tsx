/**
 * Register Page
 * =============
 * Страница регистрации нового пользователя.
 */

import { useNavigate } from 'react-router-dom';
import { RegisterForm } from '../../components/forms';
import { AuthLayout } from '../../components/layout';

export function RegisterPage() {
  const navigate = useNavigate();

  const handleSuccess = () => {
    navigate('/verify-email/pending');
  };

  return (
    <AuthLayout mode="register"><RegisterForm onSuccess={handleSuccess} /></AuthLayout>
  );
}
