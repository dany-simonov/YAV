/**
 * Register Form Component
 * =======================
 * Форма регистрации с валидацией и интеграцией с Appwrite.
 */

import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Button, Input, Alert } from '../ui';
import { useAuthStore } from '../../store';
import { isValidEmail, normalizeEmail, validatePassword } from '../../lib/utils';

interface RegisterFormProps {
  onSuccess?: () => void;
}

interface FormErrors {
  name?: string;
  email?: string;
  password?: string;
  confirmPassword?: string;
}

export function RegisterForm({ onSuccess }: RegisterFormProps) {
  const { register, isActionLoading, error, clearError } = useAuthStore();
  
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [errors, setErrors] = useState<FormErrors>({});

  // Validation
  const validate = (): boolean => {
    const newErrors: FormErrors = {};
    
    // Name validation
    if (!name.trim()) {
      newErrors.name = 'Имя обязательно';
    } else if (name.trim().length < 2) {
      newErrors.name = 'Минимум 2 символа';
    } else if (name.trim().length > 50) {
      newErrors.name = 'Максимум 50 символов';
    }
    
    // Email validation
    if (!email) {
      newErrors.email = 'Email обязателен';
    } else if (!isValidEmail(email)) {
      newErrors.email = 'Некорректный формат email';
    }
    
    // Password validation
    const passwordValidation = validatePassword(password);
    if (!password) {
      newErrors.password = 'Пароль обязателен';
    } else if (!passwordValidation.valid) {
      newErrors.password = passwordValidation.message;
    }
    
    // Confirm password
    if (!confirmPassword) {
      newErrors.confirmPassword = 'Подтвердите пароль';
    } else if (password !== confirmPassword) {
      newErrors.confirmPassword = 'Пароли не совпадают';
    }
    
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handle submit
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    clearError();
    
    if (!validate()) return;
    
    const result = await register(name.trim(), normalizeEmail(email), password);
    
    if (result.success) {
      onSuccess?.();
    }
  };

  // Password strength indicator
  const getPasswordStrength = (pwd: string) => {
    if (pwd.length === 0) return { level: 0, text: '', color: '' };
    if (pwd.length < 8) return { level: 1, text: 'Слабый', color: 'bg-mv-fake' };
    if (pwd.length < 12) return { level: 2, text: 'Средний', color: 'bg-mv-uncertain' };
    return { level: 3, text: 'Сильный', color: 'bg-mv-real' };
  };

  const strength = getPasswordStrength(password);

  return (
    <div className="w-full">
      <Link to="/" className="lg:hidden inline-flex items-center gap-2.5 mb-5"><span className="w-10 h-10 rounded-[11px] bg-white border border-black/[.12] flex items-center justify-center"><img src="/assets/img/yav-logo.png" alt="" className="w-8 h-8 object-contain" /></span><strong>ЯВЬ</strong></Link>
      <div className="mb-5"><p className="eyebrow mb-3">Регистрация</p><h2 className="text-[34px] sm:text-[40px] leading-[1.02] tracking-[-.05em] font-semibold">Создать аккаунт в ЯВЬ</h2><p className="mt-3 text-base text-mv-text-secondary">Уже есть аккаунт? <Link to="/login" className="underline underline-offset-4 hover:text-black">Войти</Link></p></div>

      {/* Error Alert */}
      {error && (
        <Alert variant="error" className="mb-4" onClose={clearError}>
          {error}
        </Alert>
      )}

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-3">
        <Input
          label="Имя"
          type="text"
          placeholder="Ваше имя"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (errors.name) setErrors((prev) => ({ ...prev, name: undefined }));
          }}
          error={errors.name}
          size="lg" className="!bg-white !rounded-[10px] !border-black/[.08] !px-4 !py-3 !text-base shadow-[0_2px_3px_rgba(0,0,0,.03),0_8px_22px_rgba(0,0,0,.06)]"
          autoComplete="name"
          disabled={isActionLoading}
        />

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
          size="lg" className="!bg-white !rounded-[10px] !border-black/[.08] !px-4 !py-3 !text-base shadow-[0_2px_3px_rgba(0,0,0,.03),0_8px_22px_rgba(0,0,0,.06)]"
          autoComplete="email"
          inputMode="email"
          autoCapitalize="none"
          spellCheck={false}
          disabled={isActionLoading}
        />

        <div>
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
            size="lg" className="!bg-white !rounded-[10px] !border-black/[.08] !px-4 !py-3 !text-base shadow-[0_2px_3px_rgba(0,0,0,.03),0_8px_22px_rgba(0,0,0,.06)]"
            autoComplete="new-password"
            disabled={isActionLoading}
          />
          
          {/* Password strength indicator */}
          {password && (
            <div className="mt-1.5 flex items-center gap-3">
              <div className="flex flex-1 gap-1">
                {[1, 2, 3].map((level) => (
                  <div
                    key={level}
                    className={`h-1 flex-1 rounded-full transition-colors ${
                      strength.level >= level ? strength.color : 'bg-mv-border'
                    }`}
                  />
                ))}
              </div>
              <span className={`text-[11px] ${
                strength.level === 1 ? 'text-mv-fake' :
                strength.level === 2 ? 'text-mv-uncertain' :
                'text-mv-real'
              }`}>
                {strength.text}
              </span>
            </div>
          )}
        </div>

        <Input
          label="Подтверждение пароля"
          type="password"
          placeholder="Повторите пароль"
          value={confirmPassword}
          onChange={(e) => {
            setConfirmPassword(e.target.value);
            if (errors.confirmPassword) setErrors((prev) => ({ ...prev, confirmPassword: undefined }));
          }}
          error={errors.confirmPassword}
          size="lg" className="!bg-white !rounded-[10px] !border-black/[.08] !px-4 !py-3 !text-base shadow-[0_2px_3px_rgba(0,0,0,.03),0_8px_22px_rgba(0,0,0,.06)]"
          autoComplete="new-password"
          disabled={isActionLoading}
        />

        <Button
          type="submit"
          fullWidth
          size="lg" className="!min-h-[48px] !rounded-[10px] !bg-black !text-sm"
          isLoading={isActionLoading}
        >
          Создать аккаунт
        </Button>
      </form>

      {/* Terms */}
      <p className="mt-4 text-[11px] leading-5 text-mv-text-muted text-center">
        Регистрируясь, вы соглашаетесь с{' '}
        <Link to="/terms" className="text-mv-accent hover:underline">
          Условиями использования
        </Link>{' '}
        и{' '}
        <Link to="/privacy" className="text-mv-accent hover:underline">
          Политикой конфиденциальности
        </Link>
      </p>

      <Link to="/" className="block mt-3 text-center text-xs text-mv-text-muted underline underline-offset-4 hover:text-black">Вернуться на сайт</Link>
    </div>
  );
}
