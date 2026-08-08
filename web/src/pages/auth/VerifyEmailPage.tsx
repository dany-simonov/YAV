import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, MailCheck, RefreshCw, ShieldCheck } from 'lucide-react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Button } from '../../components/ui';
import { useAuthStore } from '../../store';

function VerificationShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen bg-mv-bg flex items-center justify-center px-4 py-12">
      <section className="w-full max-w-[640px] soft-card p-7 sm:p-11">
        <Link to="/" className="inline-flex items-center gap-3 font-semibold text-xl tracking-[-.03em]">
          <span className="w-9 h-9 rounded-[10px] border border-black/15 bg-white flex items-center justify-center shadow-sm">
            <span className="w-3 h-3 rounded-full border border-black" />
          </span>
          ЯВЬ
        </Link>
        {children}
      </section>
    </main>
  );
}

export function EmailVerificationPendingPage() {
  const { user, sendEmailVerification, isActionLoading, logout } = useAuthStore();
  const navigate = useNavigate();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resend = async () => {
    setMessage(null);
    setError(null);
    const result = await sendEmailVerification();
    if (result.success) setMessage('Новое письмо отправлено. Проверьте папку «Спам», если его нет во входящих.');
    else setError(result.error ?? 'Не удалось отправить письмо');
  };

  if (user?.emailVerification) {
    return (
      <VerificationShell>
        <CheckCircle2 className="mt-12 text-mv-real" size={34} />
        <p className="eyebrow mt-7">E-mail подтверждён</p>
        <h1 className="section-title mt-4">Аккаунт готов к работе</h1>
        <Link to="/dashboard" className="btn-black mt-8">Перейти в рабочую область</Link>
      </VerificationShell>
    );
  }

  return (
    <VerificationShell>
      <MailCheck className="mt-12" size={34} />
      <p className="eyebrow mt-7">Подтверждение аккаунта</p>
      <h1 className="section-title mt-4">Проверьте почту</h1>
      <p className="mt-6 text-lg leading-8 text-mv-text-secondary">
        На ваш e-mail отправлена ссылка для подтверждения аккаунта. Пожалуйста, перейдите по ней для завершения регистрации.
      </p>
      {user?.email && <p className="mt-5 font-semibold break-all">{user.email}</p>}

      <div className="mt-8 rounded-xl border border-mv-uncertain/25 bg-mv-uncertain/5 p-5">
        <p className="font-semibold">До подтверждения недоступны:</p>
        <p className="mt-2 text-sm leading-6 text-mv-text-secondary">личная история проверок и отправка сложных мультимодальных запросов.</p>
      </div>

      {message && <p role="status" className="mt-5 text-sm text-mv-real">{message}</p>}
      {error && <p role="alert" className="mt-5 text-sm text-mv-fake">{error}</p>}

      <div className="mt-8 flex flex-col sm:flex-row gap-3">
        {user ? (
          <Button onClick={resend} isLoading={isActionLoading} leftIcon={<RefreshCw size={16} />}>
            Отправить письмо повторно
          </Button>
        ) : (
          <Link to="/login" className="btn-black">Войти и отправить письмо</Link>
        )}
        <Link to="/" className="btn-light">Вернуться на сайт</Link>
      </div>

      {user && (
        <button
          type="button"
          onClick={async () => { await logout(); navigate('/login'); }}
          className="mt-6 text-sm text-mv-text-secondary underline underline-offset-4"
        >
          Войти под другим аккаунтом
        </button>
      )}
    </VerificationShell>
  );
}

function readVerificationParams(locationSearch: string) {
  const candidates = [
    locationSearch,
    window.location.search,
    window.location.hash.includes('?') ? `?${window.location.hash.split('?')[1]}` : '',
  ];

  for (const candidate of candidates) {
    const params = new URLSearchParams(candidate);
    const userId = params.get('userId');
    const secret = params.get('secret');
    if (userId && secret) return { userId, secret };
  }

  return null;
}

export function VerifyEmailPage() {
  const location = useLocation();
  const { confirmEmailVerification } = useAuthStore();
  const started = useRef(false);
  const [state, setState] = useState<'loading' | 'success' | 'error'>('loading');
  const [error, setError] = useState('');

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const params = readVerificationParams(location.search);
    if (!params) {
      setError('В ссылке не хватает данных подтверждения. Запросите новое письмо.');
      setState('error');
      return;
    }

    void confirmEmailVerification(params.userId, params.secret).then((result) => {
      if (result.success) setState('success');
      else {
        setError(result.error ?? 'Ссылка недействительна или уже была использована.');
        setState('error');
      }
    });
  }, [confirmEmailVerification, location.search]);

  return (
    <VerificationShell>
      {state === 'loading' && (
        <div className="py-20 text-center">
          <RefreshCw className="mx-auto animate-spin" size={32} />
          <h1 className="text-3xl font-semibold tracking-[-.04em] mt-6">Подтверждаем e-mail…</h1>
        </div>
      )}

      {state === 'success' && (
        <div className="pt-12">
          <ShieldCheck className="text-mv-real" size={36} />
          <p className="eyebrow mt-7">Готово</p>
          <h1 className="section-title mt-4">E-mail подтверждён</h1>
          <p className="mt-6 text-lg text-mv-text-secondary">Теперь доступны история проверок и комплексный анализ материалов.</p>
          <Link to="/dashboard" className="btn-black mt-8">Перейти в рабочую область</Link>
        </div>
      )}

      {state === 'error' && (
        <div className="pt-12">
          <MailCheck size={36} />
          <p className="eyebrow mt-7">Не удалось подтвердить</p>
          <h1 className="section-title mt-4">Проверьте ссылку</h1>
          <p role="alert" className="mt-6 text-lg text-mv-text-secondary">{error}</p>
          <Link to="/verify-email/pending" className="btn-black mt-8">Запросить новое письмо</Link>
        </div>
      )}
    </VerificationShell>
  );
}
