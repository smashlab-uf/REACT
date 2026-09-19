import axios from 'axios';
import * as SecureStore from 'expo-secure-store';
import { API_KEY, BASE_URL } from '../api/config';
import { user } from '../api/endpoints';
import { createPushRegistration } from './registration';

const KEY = 'push_registration';

export const pushRegistration = createPushRegistration({
  read: async () => {
    const raw = await SecureStore.getItemAsync(KEY);
    return raw ? JSON.parse(raw) : null;
  },
  write: async (record) => {
    if (record) await SecureStore.setItemAsync(KEY, JSON.stringify(record));
    else await SecureStore.deleteItemAsync(KEY);
  },
  register: (userId, token) => user.update(userId, { push_token: token }),
  // Bypass JWT refresh/logout interceptors: revocation also works after expiry.
  unregister: (userId, token) => axios.post(`${BASE_URL}/notifications/unregister/`, {
    user_id: userId, push_token: token,
  }, { headers: { 'X-API-Key': API_KEY }, timeout: 10000 }),
});
