import { useState, useEffect, useCallback } from 'react';
import { account, ID } from '../lib/appwrite';
import type { User } from '../types';
import type { Models } from 'appwrite';

/** @deprecated Legacy hook; it is not used by the active Router. Use useAuthStore instead. */
export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const checkAuth = useCallback(async () => {
    try {
      const session = await account.get() as Models.User<Models.Preferences>;
      setUser({
        $id: session.$id,
        email: session.email,
        name: session.name || session.email.split('@')[0],
      });
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  const login = async (email: string, password: string) => {
    try {
      await account.createEmailPasswordSession(email, password);
      await checkAuth();
      return { success: true };
    } catch (error) {
      // Try to create account if login fails
      try {
        await account.create(ID.unique(), email, password);
        await account.createEmailPasswordSession(email, password);
        await checkAuth();
        return { success: true };
      } catch (createError) {
        return { success: false, error: 'Неверный email или пароль' };
      }
    }
  };

  const logout = async () => {
    try {
      await account.deleteSession('current');
      setUser(null);
    } catch (error) {
      console.error('Logout error:', error);
    }
  };

  return { user, loading, login, logout, checkAuth };
}
