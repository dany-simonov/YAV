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

interface AuthActions {
  /** Initialize auth state (check existing session) */
  initialize: () => Promise<void>;
  
  /** Login with email and password */
  login: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  
  /** Register new user */
  register: (name: string, email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  
  /** Logout current user */
  logout: () => Promise<void>;
  
  /** Clear error */
  clearError: () => void;
  
  /** Refresh user data */
  refreshUser: () => Promise<void>;
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
const getErrorMessage = (error: unknown): string => {
  // Handle Appwrite-specific exceptions
  if (error instanceof AppwriteException) {
    const { code, type, message } = error;
    
    // Log error details in development
    if (import.meta.env.DEV) {
      console.warn('[Auth Error]', { code, type, message });
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
        // User not found
        if (type === 'user_not_found') {
          return 'Пользователь с таким email не найден';
        }
        return 'Запрашиваемый ресурс не найден';
        
      case 409:
        // Conflict - user already exists
        if (type === 'user_already_exists') {
          return 'Пользователь с таким email уже существует';
        }
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
    
    // Return original message if nothing matched
    return message || 'Произошла ошибка при авторизации';
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
    
    // Log unexpected errors in development
    if (import.meta.env.DEV) {
      console.error('[Auth Unexpected Error]', error);
    }
    
    return error.message;
  }
  
  return 'Произошла неизвестная ошибка';
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

export const useAuthStore = create<AuthStore>((set) => ({
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
      
      if (import.meta.env.DEV) {
        console.log('[Auth] Attempting login for:', email);
      }
      
      // Create email/password session
      const session = await account.createEmailPasswordSession(email, password);
      sessionCreated = true;
      
      if (import.meta.env.DEV) {
        console.log('[Auth] Session created:', session.$id);
      }
      
      // Fetch user data
      const appwriteUser = await account.get();
      await ensureUserProfile();
      const user = transformUser(appwriteUser);
      
      if (import.meta.env.DEV) {
        console.log('[Auth] Login successful:', user.email);
      }
      
      set({ 
        user,
        session,
        isActionLoading: false 
      });
      
      return { success: true };
    } catch (error) {
      if (sessionCreated) await discardCurrentSession();
      const errorMessage = getErrorMessage(error);
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
      
      if (import.meta.env.DEV) {
        console.log('[Auth] Attempting registration for:', email);
      }
      
      // Step 1: Create new user account
      const newUser = await account.create(ID.unique(), email, password, name);
      
      if (import.meta.env.DEV) {
        console.log('[Auth] User created:', newUser.$id);
      }
      
      // Step 2: Automatically log in after registration
      const session = await account.createEmailPasswordSession(email, password);
      sessionCreated = true;
      
      if (import.meta.env.DEV) {
        console.log('[Auth] Auto-login session created:', session.$id);
      }
      
      // Step 3: Fetch full user data
      const appwriteUser = await account.get();
      await ensureUserProfile();
      const user = transformUser(appwriteUser);
      
      if (import.meta.env.DEV) {
        console.log('[Auth] Registration complete:', user.email);
      }
      
      set({ 
        user,
        session,
        isActionLoading: false 
      });
      
      return { success: true };
    } catch (error) {
      if (sessionCreated) await discardCurrentSession();
      const errorMessage = getErrorMessage(error);
      set({ 
        isActionLoading: false, 
        error: errorMessage 
      });
      return { success: false, error: errorMessage };
    }
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
    } catch (error) {
      console.error('Logout error:', error);
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
      const user = transformUser(appwriteUser);
      set({ user });
    } catch {
      set({ user: null, session: null });
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
