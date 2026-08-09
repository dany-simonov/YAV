/**
 * Register Form Component
 * =======================
 * Форма регистрации с валидацией и интеграцией с Appwrite.
 */

import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Mail, Lock, User, UserPlus } from 'lucide-react';
import { Button, Input, Alert, Card } from '../ui';
import { useAuthStore, type AuthActionResult } from '../../store';
import { isValidEmail, validatePassword } from '../../lib/utils';
import { normalizeDisplayName } from '../../lib/profileValidation';

interface RegisterFormProps {
  onSuccess?: (result: AuthActionResult) => void;
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
    const normalizedName = normalizeDisplayName(name);
    if (!name.trim()) {
      newErrors.name = 'Имя обязательно';
    } else if (!normalizedName) {
      newErrors.name = 'Имя должно содержать от 2 до 50 символов без управляющих символов';
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
    
    const normalizedName = normalizeDisplayName(name);
    if (!normalizedName) return;
    const result = await register(normalizedName, email.trim(), password);
    
    if (result.success) {
      onSuccess?.(result);
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
    <Card variant="elevated" padding="lg" className="w-full max-w-md mx-auto">
      {/* Header */}
      <div className="text-center mb-8">
        <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-black flex items-center justify-center">
          <UserPlus className="w-8 h-8 text-white" />
        </div>
        <h1 className="text-2xl font-bold text-mv-text">Создать аккаунт</h1>
        <p className="mt-2 text-mv-text-secondary">
          Зарегистрируйтесь для получения бесплатных проверок
        </p>
      </div>

      {/* Error Alert */}
      {error && (
        <Alert variant="error" className="mb-6" onClose={clearError}>
          {error}
        </Alert>
      )}

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-5">
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
          leftIcon={<User className="w-5 h-5" />}
          autoComplete="name"
          disabled={isActionLoading}
        />

        <Input
          label="Email"
          type="email"
          placeholder="your@email.com"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (errors.email) setErrors((prev) => ({ ...prev, email: undefined }));
          }}
          error={errors.email}
          leftIcon={<Mail className="w-5 h-5" />}
          autoComplete="email"
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
            leftIcon={<Lock className="w-5 h-5" />}
            autoComplete="new-password"
            disabled={isActionLoading}
          />
          
          {/* Password strength indicator */}
          {password && (
            <div className="mt-2">
              <div className="flex gap-1 mb-1">
                {[1, 2, 3].map((level) => (
                  <div
                    key={level}
                    className={`h-1 flex-1 rounded-full transition-colors ${
                      strength.level >= level ? strength.color : 'bg-mv-border'
                    }`}
                  />
                ))}
              </div>
              <span className={`text-xs ${
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
          leftIcon={<Lock className="w-5 h-5" />}
          autoComplete="new-password"
          disabled={isActionLoading}
        />

        <Button
          type="submit"
          fullWidth
          size="lg"
          isLoading={isActionLoading}
        >
          Создать аккаунт
        </Button>
      </form>

      {/* Terms */}
      <p className="mt-6 text-xs text-mv-text-muted text-center">
        Регистрируясь, вы соглашаетесь с{' '}
        <Link to="/terms" className="text-mv-accent hover:underline">
          Условиями использования
        </Link>{' '}
        и{' '}
        <Link to="/privacy" className="text-mv-accent hover:underline">
          Политикой конфиденциальности
        </Link>
      </p>

      {/* Footer Links */}
      <div className="mt-8 text-center text-sm">
        <span className="text-mv-text-secondary">Уже есть аккаунт? </span>
        <Link
          to="/login"
          className="text-mv-accent hover:text-mv-accent-hover font-medium transition-colors"
        >
          Войти
        </Link>
      </div>
    </Card>
  );
}
