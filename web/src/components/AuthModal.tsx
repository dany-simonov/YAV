/**
 * Auth Modal Component
 * ====================
 * Модальное окно авторизации с переключением между входом и регистрацией.
 * Использует useAuthStore напрямую для вызова login/register.
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../store/authStore';
import { isValidEmail, normalizeEmail } from '../lib/utils';
import { X, XCircle, Loader2 } from 'lucide-react';
interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function AuthModal({ isOpen, onClose }: AuthModalProps) {
  const navigate = useNavigate();
  // ============================================================================
  // Store Integration
  // ============================================================================
  const { login, register, error, isActionLoading, clearError } = useAuthStore();

  // ============================================================================
  // Local State
  // ============================================================================
  /** true = Login mode, false = Register mode */
  const [isLoginMode, setIsLoginMode] = useState(true);
  
  // Form fields
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [emailError, setEmailError] = useState('');

  // ============================================================================
  // Effects
  // ============================================================================
  
  // Clear form and errors when modal opens/closes
  useEffect(() => {
    if (!isOpen) {
      setName('');
      setEmail('');
      setPassword('');
      setEmailError('');
      clearError();
      setIsLoginMode(true);
    }
  }, [isOpen, clearError]);

  // ============================================================================
  // Handlers
  // ============================================================================

  /** Toggle between login and register modes */
  const handleToggleMode = () => {
    setIsLoginMode((prev) => !prev);
    clearError(); // Always clear errors when switching modes
    setName('');
    setPassword('');
    setEmailError('');
  };

  /** Handle form submission */
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const normalizedEmail = normalizeEmail(email);
    if (!isValidEmail(normalizedEmail)) {
      setEmailError('Введите корректный e-mail с доменной зоной, например name@company.ru');
      return;
    }
    setEmailError('');

    let result: { success: boolean; error?: string };

    if (isLoginMode) {
      // Login mode: call login(email, password)
      result = await login(normalizedEmail, password);
    } else {
      // Register mode: call register(name, email, password)
      result = await register(name.trim(), normalizedEmail, password);
    }

    if (result.success) {
      // Success - close modal and reset form
      onClose();
      setName('');
      setEmail('');
      setPassword('');
      if (!isLoginMode) navigate('/verify-email/pending');
    }
    // If failed, error is automatically set in store and displayed
  };

  // ============================================================================
  // Render
  // ============================================================================

  if (!isOpen) return null;

  const modalTitle = isLoginMode ? 'Вход в ЯВЬ' : 'Регистрация в ЯВЬ';
  const submitButtonText = isLoginMode ? 'Войти' : 'Создать аккаунт';
  const loadingText = isLoginMode ? 'Вход...' : 'Создание...';
  const toggleText = isLoginMode 
    ? 'Нет аккаунта? Зарегистрироваться' 
    : 'Уже есть аккаунт? Войти';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md bg-mv-surface border border-mv-border rounded-xl p-6 animate-fade-in">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-mv-text-muted hover:text-mv-text"
          aria-label="Закрыть"
        >
          <X className="w-5 h-5" />
        </button>

        <h2 className="text-xl font-semibold text-mv-text mb-6">{modalTitle}</h2>

        <form onSubmit={handleSubmit} className="space-y-4">
            {/* Error Alert */}
            {error && (
              <div className="p-3 bg-mv-fake/10 border border-mv-fake/20 rounded-lg text-sm text-mv-fake flex items-start gap-2">
                <XCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}
            
            {/* Name field - only in Register mode */}
            {!isLoginMode && (
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-mv-text-secondary mb-2">
                  Имя
                </label>
                <input
                  type="text"
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required={!isLoginMode}
                  minLength={2}
                  maxLength={50}
                  placeholder="Ваше имя"
                  disabled={isActionLoading}
                  className="w-full px-4 py-3 bg-mv-surface-2 border border-mv-border rounded-lg text-mv-text placeholder:text-mv-text-muted focus:border-mv-accent focus:outline-none transition-colors disabled:opacity-50"
                />
              </div>
            )}

            {/* Email field */}
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-mv-text-secondary mb-2">
                Email
              </label>
              <input
                type="email"
                id="email"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value.replace(/\s/g, '').toLowerCase());
                  if (emailError) setEmailError('');
                }}
                required
                placeholder="name@company.ru"
                inputMode="email"
                autoCapitalize="none"
                spellCheck={false}
                disabled={isActionLoading}
                className="w-full px-4 py-3 bg-mv-surface-2 border border-mv-border rounded-lg text-mv-text placeholder:text-mv-text-muted focus:border-mv-accent focus:outline-none transition-colors disabled:opacity-50"
              />
              {emailError && <p className="mt-2 text-sm text-mv-fake">{emailError}</p>}
            </div>

            {/* Password field */}
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-mv-text-secondary mb-2">
                Пароль
              </label>
              <input
                type="password"
                id="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                placeholder="Минимум 8 символов"
                disabled={isActionLoading}
                className="w-full px-4 py-3 bg-mv-surface-2 border border-mv-border rounded-lg text-mv-text placeholder:text-mv-text-muted focus:border-mv-accent focus:outline-none transition-colors disabled:opacity-50"
              />
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={isActionLoading}
              className="w-full py-3 bg-mv-accent text-white rounded-lg font-medium hover:bg-mv-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
            >
              {isActionLoading ? (
                <>
                  {/* Spinner */}
                  <Loader2 className="animate-spin h-5 w-5" />
                  <span>{loadingText}</span>
                </>
              ) : (
                submitButtonText
              )}
            </button>

            {/* Toggle Login/Register Mode */}
            <div className="text-center pt-2">
              <button
                type="button"
                onClick={handleToggleMode}
                disabled={isActionLoading}
                className="text-sm text-mv-accent hover:text-mv-accent-hover transition-colors disabled:opacity-50"
              >
                {toggleText}
              </button>
            </div>
        </form>
      </div>
    </div>
  );
}
