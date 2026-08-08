import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { LogOut, Mail } from 'lucide-react';

import { Alert, Button, Card } from '../../components/ui';
import { useAuthStore } from '../../store';

interface VerificationRouteState {
  verificationEmailSent?: boolean;
  verificationError?: string;
  notice?: string;
}

export function VerifyEmailPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const routeState = (location.state || {}) as VerificationRouteState;
  const { user, resendVerification, logout, isActionLoading } = useAuthStore();
  const [message, setMessage] = useState<string | null>(
    routeState.notice || (routeState.verificationEmailSent ? 'Письмо отправлено.' : null)
  );
  const [error, setError] = useState<string | null>(routeState.verificationError || null);

  const handleResend = async () => {
    setMessage(null);
    setError(null);
    const result = await resendVerification();
    if (result.success) {
      setMessage(
        result.user?.emailVerification
          ? 'Email уже подтверждён.'
          : 'Новое письмо отправлено. Проверьте входящие и папку «Спам».'
      );
      if (result.user?.emailVerification) navigate('/dashboard', { replace: true });
      return;
    }
    setError(result.error || 'Не удалось отправить письмо. Попробуйте позже.');
  };

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="min-h-screen bg-mv-bg flex items-center justify-center p-4">
      <Card variant="elevated" padding="lg" className="w-full max-w-lg text-center">
        <div className="w-16 h-16 mx-auto mb-5 rounded-2xl bg-black flex items-center justify-center">
          <Mail className="w-8 h-8 text-white" />
        </div>
        <h1 className="text-2xl font-bold text-mv-text">Подтвердите email</h1>
        <p className="mt-3 text-mv-text-secondary">
          Мы отправили ссылку подтверждения на <strong>{user?.email}</strong>.
        </p>
        <p className="mt-2 text-sm text-mv-text-muted">
          До подтверждения почты запуск анализа недоступен.
        </p>

        {message && <Alert variant="success" className="mt-6 text-left">{message}</Alert>}
        {error && <Alert variant="error" className="mt-6 text-left">{error}</Alert>}

        <div className="mt-7 space-y-3">
          <Button fullWidth onClick={handleResend} isLoading={isActionLoading}>
            Отправить письмо повторно
          </Button>
          <Button fullWidth variant="ghost" onClick={handleLogout} leftIcon={<LogOut className="w-4 h-4" />}>
            Выйти
          </Button>
        </div>
      </Card>
    </div>
  );
}
