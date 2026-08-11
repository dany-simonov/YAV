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
      <div className="mb-9"><p className="eyebrow mb-5">Вход в систему</p><h2 className="text-[42px] sm:text-[52px] leading-[1.04] tracking-[-.05em] font-semibold">Продолжить в<br/>ЯВЬ</h2><p className="mt-6 text-lg text-mv-text-secondary">Нет аккаунта? <Link to="/register" className="underline underline-offset-4 hover:text-black">Создать</Link></p></div>

      {/* Error Alert */}
      {error && (
        <Alert variant="error" className="mb-6" onClose={clearError}>
          {error}
        </Alert>
      )}

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-5">
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
          className="!bg-white !rounded-[10px] !border-black/[.07] !px-5 !py-4 !text-lg shadow-[0_2px_3px_rgba(0,0,0,.04),0_12px_28px_rgba(0,0,0,.08)]"
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
          className="!bg-white !rounded-[10px] !border-black/[.07] !px-5 !py-4 !text-lg shadow-[0_2px_3px_rgba(0,0,0,.04),0_12px_28px_rgba(0,0,0,.08)]"
          autoComplete="current-password"
          disabled={isActionLoading}
        />

        <Button
          type="submit"
          fullWidth
          size="lg" className="!min-h-[58px] !rounded-[11px] !bg-black !text-base"
          isLoading={isActionLoading}
        >
          Войти
        </Button>
      </form>

      <Link to="/" className="btn-light w-full mt-3 !min-h-[58px] !text-base">Вернуться на сайт</Link>
      <p className="mt-7 text-xs sm:text-sm text-mv-text-muted leading-6">Продолжая, вы соглашаетесь с <Link to="/terms" className="underline">условиями использования</Link> и <Link to="/privacy" className="underline">политикой конфиденциальности</Link>.</p>
    </div>
  );
}
