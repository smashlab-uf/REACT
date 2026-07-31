import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';
import { setTokens, clearTokens } from '../api/client';
import { auth } from '../api/endpoints';

const KEYS = {
  tokens: 'auth_tokens',
  user: 'auth_user',
};

type User = {
  user_id: number;
  email: string;
  first_name: string;
  last_name: string;
};

type AuthStore = {
  user: User | null;
  userId: number | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (access: string, refresh: string, user: User) => Promise<void>;
  logout: () => Promise<void>;
  restoreSession: () => Promise<void>;
};

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  userId: null,
  isAuthenticated: false,
  isLoading: true,

  login: async (access, refresh, user) => {
    await SecureStore.setItemAsync(KEYS.tokens, JSON.stringify({ access, refresh }));
    await SecureStore.setItemAsync(KEYS.user, JSON.stringify(user));
    setTokens(access, refresh);
    set({ user, userId: user.user_id, isAuthenticated: true });
  },

  logout: async () => {
    await SecureStore.deleteItemAsync(KEYS.tokens);
    await SecureStore.deleteItemAsync(KEYS.user);
    clearTokens();
    set({ user: null, userId: null, isAuthenticated: false });
  },

  restoreSession: async () => {
    try {
      const tokensJson = await SecureStore.getItemAsync(KEYS.tokens);
      const userJson = await SecureStore.getItemAsync(KEYS.user);

      if (tokensJson && userJson) {
        const { access, refresh } = JSON.parse(tokensJson);
        const cachedUser = JSON.parse(userJson) as User;
        setTokens(access, refresh);

        // Set cached user immediately so app loads fast
        set({ user: cachedUser, userId: cachedUser.user_id, isAuthenticated: true });

        // Refresh profile from server in the background
        try {
          console.log('[Auth] Calling /auth/me/...');
          const res = await auth.me();
          const freshUser = res.data as User;
          await SecureStore.setItemAsync(KEYS.user, JSON.stringify(freshUser));
          set({ user: freshUser, userId: freshUser.user_id });
          console.log('[Auth] Profile refreshed:', freshUser.email);
        } catch (e: any) {
          console.log('[Auth] /auth/me/ failed:', e?.response?.status, e?.message);
        }
      }
    } catch {
      // Corrupted storage — start fresh
    } finally {
      set({ isLoading: false });
    }
  },
}));
