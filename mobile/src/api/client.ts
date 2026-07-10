import axios, { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse } from 'axios';
import * as SecureStore from 'expo-secure-store';
import { BASE_URL } from './config';

// Token storage — replaced by expo-secure-store when auth is wired up
let accessToken: string | null = null;
let refreshToken: string | null = null;

export function setTokens(access: string, refresh: string) {
  accessToken = access;
  refreshToken = refresh;
}

export function clearTokens() {
  accessToken = null;
  refreshToken = null;
}

const client: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 10000,
});

// Attach access token + log request
client.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  console.log(`[API →] ${config.method?.toUpperCase()} ${config.baseURL}${config.url}`, config.data ?? '');
  return config;
});

// Log response + auto-refresh on 401
client.interceptors.response.use(
  (response: AxiosResponse) => {
    console.log(`[API ←] ${response.status} ${response.config.url}`, response.data);
    return response;
  },
  async (error) => {
    console.log(`[API ✗] ${error.response?.status ?? 'ERR'} ${error.config?.url}`, error.response?.data ?? error.message);
    const original = error.config;

    // If accessToken is null, try loading tokens from SecureStore before giving up
    if (!refreshToken) {
      const stored = await SecureStore.getItemAsync('auth_tokens').catch(() => null);
      if (stored) {
        const parsed = JSON.parse(stored);
        accessToken = parsed.access;
        refreshToken = parsed.refresh;
      }
    }

    if (error.response?.status === 401 && !original._retry && refreshToken) {
      original._retry = true;
      try {
        const res = await axios.post(`${BASE_URL}/auth/token/refresh/`, {
          refresh: refreshToken,
        });
        accessToken = res.data.access;
        // Persist the new access token back to SecureStore
        const stored = await SecureStore.getItemAsync('auth_tokens').catch(() => null);
        if (stored) {
          const parsed = JSON.parse(stored);
          await SecureStore.setItemAsync('auth_tokens', JSON.stringify({ ...parsed, access: accessToken }));
        }
        original.headers.Authorization = `Bearer ${accessToken}`;
        return client(original);
      } catch {
        clearTokens();
        // Caller handles navigation to login
      }
    }

    return Promise.reject(error);
  }
);

export default client;
