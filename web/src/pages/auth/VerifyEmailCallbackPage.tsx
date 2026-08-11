import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { CheckCircle2, MailWarning } from 'lucide-react';

import { Alert, Button, Card, Spinner } from '../../components/ui';
import { consumeEmailVerificationToken } from '../../lib/emailVerification';
import { useAuthStore, type VerificationSessionState } from '../../store';

type CallbackState = 'verifying' | 'success' | 'already' | 'missing' | 'invalid' | 'network';

export function VerifyEmailCallbackPage() {
  const navigate = useNavigate();
  const started = useRef(false);
  const [state, setState] = useState<CallbackState>('verifying');
  const [sessionState, setSessionState] = useState<VerificationSessionState>('none');
  const confirmVerification = useAuthStore((value) => value.confirmVerification);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const token = consumeEmailVerificationToken();
    if (!token) {
      setState('missing');
      return;
    }

    void confirmVerification(token.userId, token.secret).then((result) => {
      if (!result.success) {
        setState(result.reason === 'network' ? 'network' : 'invalid');
        return;
      }

      const nextSessionState = result.sessionState || 'none';
      setSessionState(nextSessionState);
      setState(result.alreadyVerified ? 'already' : 'success');
      if (nextSessionState === 'same') {
        window.setTimeout(() => navigate('/dashboard', { replace: true }), 900);
      }
    });
  }, [confirmVerification, navigate]);

  const isSuccess = state === 'success' || state === 'already';

  return (
    <div className="min-h-screen bg-mv-bg flex items-center justify-center p-4">
      <Card variant="elevated" padding="lg" className="w-full max-w-lg text-center">
        {state === 'verifying' && (
          <>
            <Spinner size="lg" className="mx-auto mb-5" />
            <h1 className="text-2xl font-bold text-mv-text">Подтверждаем email…</h1>
            <p className="mt-3 text-mv-text-secondary">Это займёт несколько секунд.</p>
          </>
        )}

        {isSuccess && (
          <>
            <CheckCircle2 className="w-14 h-14 mx-auto mb-5 text-mv-real" />
            <h1 className="text-2xl font-bold text-mv-text">
              {state === 'already' ? 'Email уже подтверждён' : 'Email подтверждён'}
            </h1>
            {sessionState === 'same' ? (
              <p className="mt-3 text-mv-text-secondary">Переходим в личный кабинет…</p>
            ) : sessionState === 'other' ? (
              <Alert variant="info" className="mt-6 text-left">
                В этом браузере открыт другой аккаунт. Выйдите из него вручную, затем войдите в подтверждённый аккаунт.
              </Alert>
            ) : (
              <div className="mt-6">
                <Link to="/login"><Button fullWidth>Войти</Button></Link>
              </div>
            )}
          </>
        )}

        {!isSuccess && state !== 'verifying' && (
          <>
            <MailWarning className="w-14 h-14 mx-auto mb-5 text-mv-uncertain" />
            <h1 className="text-2xl font-bold text-mv-text">Не удалось подтвердить email</h1>
            <Alert variant={state === 'network' ? 'warning' : 'error'} className="mt-6 text-left">
              {state === 'missing'
                ? 'В ссылке отсутствуют параметры подтверждения.'
                : state === 'network'
                  ? 'Сервис временно недоступен. Проверьте соединение и повторите попытку.'
                  : 'Ссылка недействительна или срок её действия истёк.'}
            </Alert>
            <div className="mt-6">
              <Link to="/verify-email"><Button fullWidth>Запросить новое письмо</Button></Link>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
