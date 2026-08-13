/**
 * Login Form Component
 * ====================
 * Форма входа с валидацией и интеграцией с Appwrite.
 */

import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Button, Input, Alert } from '../ui';
import { useAuthStore } from '../../store';
import { isValidEmail, normalizeEmail } from '../../lib/utils';

interface LoginFormProps {
  onSuccess?: () => void;
}

interface FormErrors {
  email?: string;
  password?: string;
}

export function LoginForm({ onSuccess }: LoginFormProps) {
  const { login, isActionLoading, error, clearError } = useAuthStore();
  
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<FormErrors>({});

  // Validation
  const validate = (): boolean => {
    const newErrors: FormErrors = {};
    
    if (!email) {
      newErrors.email = 'Email обязателен';
    } else if (!isValidEmail(email)) {
      newErrors.email = 'Некорректный формат email';
    }
    
    if (!password) {
      newErrors.password = 'Пароль обязателен';
    } else if (password.length < 8) {
      newErrors.password = 'Минимум 8 символов';
    }
    
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handle submit
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    clearError();
    
    if (!validate()) return;
    
    const result = await login(normalizeEmail(email), password);
    
    if (result.success) {
      onSuccess?.();
    }
  };

  return (
    <div className="w-full">
      <Link to="/" className="lg:hidden inline-flex items-center gap-2.5 mb-7"><span className="w-10 h-10 rounded-[11px] bg-white border border-black/[.12] flex items-center justify-center"><img src="/assets/img/yav-logo.png" alt="" className="w-8 h-8 object-contain" /></span><strong>ЯВЬ</strong></Link>
      <div className="mb-6"><p className="eyebrow mb-3">Вход в систему</p><h2 className="text-[36px] sm:text-[42px] leading-[1.02] tracking-[-.05em] font-semibold">Продолжить в ЯВЬ</h2><p className="mt-4 text-base text-mv-text-secondary">Нет аккаунта? <Link to="/register" className="underline underline-offset-4 hover:text-black">Создать</Link></p></div>

      {/* Error Alert */}
      {error && (
        <Alert variant="error" className="mb-4" onClose={clearError}>
          {error}
        </Alert>
      )}

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-3.5">
        <Input
          label="Электронная почта"
          type="email"
          placeholder="name@company.ru"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value.replace(/\s/g, '').toLowerCase());
            if (errors.email) setErrors((prev) => ({ ...prev, email: undefined }));
          }}
          error={errors.email}
          size="lg"
          className="!bg-white !rounded-[10px] !border-black/[.08] !px-4 !py-3 !text-base shadow-[0_2px_3px_rgba(0,0,0,.03),0_8px_22px_rgba(0,0,0,.06)]"
          autoComplete="email"
          inputMode="email"
          autoCapitalize="none"
          spellCheck={false}
          disabled={isActionLoading}
        />

        <Input
          label="Пароль"
          type="password"
          placeholder="Минимум 8 символов"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            if (errors.password) setErrors((prev) => ({ ...prev, password: undefined }));
          }}
          error={errors.password}
          size="lg"
          className="!bg-white !rounded-[10px] !border-black/[.08] !px-4 !py-3 !text-base shadow-[0_2px_3px_rgba(0,0,0,.03),0_8px_22px_rgba(0,0,0,.06)]"
          autoComplete="current-password"
          disabled={isActionLoading}
        />

        <Button
          type="submit"
          fullWidth
          size="lg" className="!min-h-[48px] !rounded-[10px] !bg-black !text-sm"
          isLoading={isActionLoading}
        >
          Войти
        </Button>
      </form>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-mv-text-muted"><Link to="/" className="underline underline-offset-4 hover:text-black">Вернуться на сайт</Link><span>Продолжая, вы принимаете <Link to="/terms" className="underline">условия</Link>.</span></div>
    </div>
  );
}
