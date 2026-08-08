/**
 * Authentication Store (Zustand)
 * ==============================
 * Централизованное управление состоянием аутентификации.
 * 
 * Features:
 * - Persistent session management
 * - User state
 * - Loading states
 * - Auth actions (login, register, logout)
 * - Graceful error handling with Russian messages
 */

import { create } from 'zustand';
import { AppwriteException } from 'appwrite';
import { account, ID, type Models } from '../lib/appwrite';
import { ensureUserProfile } from '../lib/serverProfile';
import {
  confirmEmailVerification,
  resendEmailVerification,
  sendEmailVerification,
} from '../lib/emailVerification';

// ============================================================================
// Types
// ============================================================================

export interface User {
  $id: string;
  email: string;
  name: string;
  emailVerification: boolean;
  phone: string;
  phoneVerification: boolean;
  createdAt: string;
}

interface AuthState {
  /** Current authenticated user */
  user: User | null;
  
  /** Session object */
  session: Models.Session | null;
  
  /** Loading state for initial auth check */
  isLoading: boolean;
  
  /** Loading state for auth actions */
  isActionLoading: boolean;
  
  /** Error message */
  error: string | null;
  
  /** Whether initial auth check is complete */
  isInitialized: boolean;
}

export interface AuthActionResult {
  success: boolean;
  error?: string;
  user?: User;
  verificationEmailSent?: boolean;
}

export type VerificationSessionState = 'same' | 'none' | 'other';

export interface ConfirmVerificationResult {
  success: boolean;
  error?: string;
  reason?: 'invalid' | 'network';
  sessionState?: VerificationSessionState;
  alreadyVerified?: boolean;
}

interface AuthActions {
  /** Initialize auth state (check existing session) */
  initialize: () => Promise<void>;
  
  /** Login with email and password */
  login: (email: string, password: string) => Promise<AuthActionResult>;
  
  /** Register new user */
  register: (name: string, email: string, password: string) => Promise<AuthActionResult>;

  /** Send an email verification message for the current account. */
  sendVerification: () => Promise<AuthActionResult>;

  /** Resend an email verification message for the current account. */
  resendVerification: () => Promise<AuthActionResult>;

  /** Confirm an email verification callback token. */
  confirmVerification: (userId: string, secret: string) => Promise<ConfirmVerificationResult>;
  
  /** Logout current user */
  logout: () => Promise<void>;
  
  /** Clear error */
  clearError: () => void;
  
  /** Refresh user data */
  refreshUser: () => Promise<User | null>;
}

type AuthStore = AuthState & AuthActions;

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Transform Appwrite user model to our User type
 */
const transformUser = (appwriteUser: Models.User<Models.Preferences>): User => ({
  $id: appwriteUser.$id,
  email: appwriteUser.email,
  name: appwriteUser.name || appwriteUser.email.split('@')[0],
  emailVerification: appwriteUser.emailVerification,
  phone: appwriteUser.phone,
  phoneVerification: appwriteUser.phoneVerification,
  createdAt: appwriteUser.$createdAt,
});

/**
 * Map Appwrite error codes to user-friendly messages
 * Uses AppwriteException for type-safe error handling
 */
type AuthOperation = 'login' | 'register' | 'verification' | 'generic';

const getErrorMessage = (error: unknown, operation: AuthOperation = 'generic'): string => {
  // Handle Appwrite-specific exceptions
  if (error instanceof AppwriteException) {
    const { code, type } = error;

    if (operation === 'login' && [400, 401, 404].includes(code)) {
      return 'Неверный email или пароль';
    }
    if (operation === 'register' && code === 409) {
      return 'Не удалось создать аккаунт с указанными данными';
    }
    if (operation === 'verification') {
      if (code === 429) return 'Слишком много попыток. Попробуйте позже';
      if (code >= 500 || code === 0) return 'Не удалось связаться с сервисом подтверждения';
      return 'Не удалось выполнить подтверждение email';
    }
    
    // Map by HTTP status code
    switch (code) {
      case 401:
        // Unauthorized - wrong credentials or no session
        if (type === 'user_invalid_credentials') {
          return 'Неверный email или пароль';
        }
        if (type === 'user_session_not_found') {
          return 'Сессия не найдена. Пожалуйста, войдите снова';
        }
        return 'Ошибка авторизации. Проверьте данные и попробуйте снова';
        
      case 404:
        return 'Запрашиваемый ресурс не найден';
        
      case 409:
        // Conflict - user already exists
        return 'Конфликт данных. Попробуйте другой email';
        
      case 429:
        // Rate limit exceeded
        return 'Слишком много попыток. Подождите несколько минут';
        
      case 500:
      case 502:
      case 503:
        // Server errors
        return 'Сервер временно недоступен. Попробуйте позже';
        
      default:
        break;
    }
    
    // Fallback: map by error type string
    if (type?.includes('password')) {
      return 'Пароль должен содержать минимум 8 символов';
    }
    if (type?.includes('email') && type?.includes('invalid')) {
      return 'Некорректный формат email';
    }
    
    return 'Произошла ошибка при авторизации';
  }
  
  // Handle generic JavaScript errors
  if (error instanceof Error) {
    const message = error.message.toLowerCase();
    
    if (message.includes('network') || message.includes('fetch')) {
      return 'Ошибка сети. Проверьте подключение к интернету';
    }
    if (message.includes('timeout')) {
      return 'Превышено время ожидания. Попробуйте снова';
    }
    
    return operation === 'verification'
      ? 'Не удалось выполнить подтверждение email'
      : 'Произошла ошибка при авторизации';
  }
  
  return 'Произошла неизвестная ошибка';
};

const getVerificationFailureReason = (error: unknown): 'invalid' | 'network' => {
  if (error instanceof AppwriteException) {
    return error.code === 0 || error.code >= 500 ? 'network' : 'invalid';
  }
  if (error instanceof Error) {
    const message = error.message.toLowerCase();
    if (message.includes('network') || message.includes('fetch') || message.includes('timeout')) {
      return 'network';
    }
  }
  return 'invalid';
};

const discardCurrentSession = async (): Promise<void> => {
  try {
    await account.deleteSession('current');
  } catch {
    // Preserve the original bootstrap error; local auth state is still cleared below.
  }
};

// ============================================================================
// Store Implementation
// ============================================================================

export const useAuthStore = create<AuthStore>((set, get) => ({
  // Initial state
  user: null,
  session: null,
  isLoading: true,
  isActionLoading: false,
  error: null,
  isInitialized: false,

  // Actions
  initialize: async () => {
    let authenticatedAccountFound = false;
    try {
      set({ isLoading: true, error: null });
      
      const appwriteUser = await account.get();
      authenticatedAccountFound = true;
      await ensureUserProfile();
      const user = transformUser(appwriteUser);
      
      set({ 
        user, 
        isLoading: false, 
        isInitialized: true 
      });
    } catch {
      if (authenticatedAccountFound) await discardCurrentSession();
      // No active session - this is expected for logged out users
      set({ 
        user: null, 
        session: null, 
        isLoading: false, 
        isInitialized: true 
      });
    }
  },

  login: async (email: string, password: string) => {
    let sessionCreated = false;
    try {
      set({ isActionLoading: true, error: null });

      // Create email/password session
      const session = await account.createEmailPasswordSession({
        email: email.trim(),
        password,
      });
      sessionCreated = true;

      // Fetch user data
      const appwriteUser = await account.get();
      await ensureUserProfile();
      const user = transformUser(appwriteUser);

      set({ 
        user,
        session,
        isActionLoading: false 
      });

      return { success: true, user };
    } catch (error) {
      if (sessionCreated) await discardCurrentSession();
      const errorMessage = getErrorMessage(error, 'login');
      set({ 
        isActionLoading: false, 
        error: errorMessage 
      });
      return { success: false, error: errorMessage };
    }
  },

  register: async (name: string, email: string, password: string) => {
    let sessionCreated = false;
    try {
      set({ isActionLoading: true, error: null });

      const normalizedEmail = email.trim();

      // Step 1: Create new user account
      await account.create({
        userId: ID.unique(),
        email: normalizedEmail,
        password,
        name,
      });

      // Step 2: Automatically log in after registration
      const session = await account.createEmailPasswordSession({
        email: normalizedEmail,
        password,
      });
      sessionCreated = true;

      // Step 3: Fetch full user data
      const appwriteUser = await account.get();
      await ensureUserProfile();
      const user = transformUser(appwriteUser);

      set({ 
        user,
        session,
      });

      if (user.emailVerification) {
        set({ isActionLoading: false });
        return { success: true, user, verificationEmailSent: false };
      }

      try {
        await sendEmailVerification();
        set({ isActionLoading: false });
        return { success: true, user, verificationEmailSent: true };
      } catch (verificationError) {
        // Account and session remain valid; the verification page offers resend.
        set({ isActionLoading: false });
        return {
          success: true,
          user,
          verificationEmailSent: false,
          error: getErrorMessage(verificationError, 'verification'),
        };
      }
    } catch (error) {
      if (sessionCreated) await discardCurrentSession();
      const errorMessage = getErrorMessage(error, 'register');
      set({ 
        isActionLoading: false, 
        error: errorMessage 
      });
      return { success: false, error: errorMessage };
    }
  },

  sendVerification: async () => {
    const currentUser = get().user;
    if (!currentUser) {
      return { success: false, error: 'Для отправки письма необходимо войти' };
    }
    if (currentUser.emailVerification) {
      return { success: true, user: currentUser, verificationEmailSent: false };
    }

    try {
      set({ isActionLoading: true, error: null });
      await sendEmailVerification();
      set({ isActionLoading: false });
      return { success: true, user: currentUser, verificationEmailSent: true };
    } catch (error) {
      const errorMessage = getErrorMessage(error, 'verification');
      set({ isActionLoading: false });
      return { success: false, error: errorMessage };
    }
  },

  resendVerification: async () => {
    const currentUser = get().user;
    if (!currentUser) {
      return { success: false, error: 'Для отправки письма необходимо войти' };
    }
    if (currentUser.emailVerification) {
      return { success: true, user: currentUser, verificationEmailSent: false };
    }

    try {
      set({ isActionLoading: true, error: null });
      await resendEmailVerification();
      set({ isActionLoading: false });
      return { success: true, user: currentUser, verificationEmailSent: true };
    } catch (error) {
      const errorMessage = getErrorMessage(error, 'verification');
      set({ isActionLoading: false });
      return { success: false, error: errorMessage };
    }
  },

  confirmVerification: async (userId: string, secret: string) => {
    if (!userId || !secret) {
      return { success: false, error: 'Ссылка подтверждения неполная' };
    }

    try {
      await confirmEmailVerification({ userId, secret });
    } catch (error) {
      try {
        const appwriteUser = await account.get();
        if (appwriteUser.$id === userId && appwriteUser.emailVerification) {
          const user = transformUser(appwriteUser);
          set({ user });
          try {
            await ensureUserProfile();
          } catch {
            // Auth is authoritative; the next bootstrap/analyze retries the mirror sync.
          }
          return { success: true, sessionState: 'same', alreadyVerified: true };
        }
      } catch {
        // A missing or unrelated session does not change the token failure result.
      }
      return {
        success: false,
        error: getErrorMessage(error, 'verification'),
        reason: getVerificationFailureReason(error),
      };
    }

    const currentStateUser = get().user;
    let appwriteUser: Models.User<Models.Preferences>;
    try {
      appwriteUser = await account.get();
    } catch {
      if (currentStateUser && currentStateUser.$id !== userId) {
        return { success: true, sessionState: 'other' };
      }
      return { success: true, sessionState: 'none' };
    }

    if (appwriteUser.$id !== userId) {
      return { success: true, sessionState: 'other' };
    }

    const user = transformUser(appwriteUser);
    set({ user });
    try {
      await ensureUserProfile();
    } catch {
      // Auth is authoritative; the next bootstrap/analyze retries the mirror sync.
    }
    return { success: true, sessionState: 'same', alreadyVerified: false };
  },

  logout: async () => {
    try {
      set({ isActionLoading: true, error: null });
      
      await account.deleteSession('current');
      
      set({ 
        user: null, 
        session: null, 
        isActionLoading: false 
      });
    } catch {
      // Even if logout fails on server, clear local state
      set({ 
        user: null, 
        session: null, 
        isActionLoading: false 
      });
    }
  },

  clearError: () => {
    set({ error: null });
  },

  refreshUser: async () => {
    try {
      const appwriteUser = await account.get();
      await ensureUserProfile();
      const user = transformUser(appwriteUser);
      set({ user });
      return user;
    } catch {
      set({ user: null, session: null });
      return null;
    }
  },
}));

// ============================================================================
// Selector Hooks
// ============================================================================

/** Check if user is authenticated */
export const useIsAuthenticated = () => useAuthStore((state) => !!state.user);

/** Get current user */
export const useUser = () => useAuthStore((state) => state.user);

/** Get loading state */
export const useAuthLoading = () => useAuthStore((state) => state.isLoading);

/** Get action loading state */
export const useAuthActionLoading = () => useAuthStore((state) => state.isActionLoading);

/** Get auth error */
export const useAuthError = () => useAuthStore((state) => state.error);
