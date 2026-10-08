import { useCallback, useEffect, useState } from 'react';
import { AppState } from 'react-native';
import * as Notifications from 'expo-notifications';
import { ema } from '../api/endpoints';
import { EMANextShowResponse } from '../api/types';
import { parsePushData, shouldOpenEMA } from '../notifications/payload';

// Availability reads never extend the server's notification-based deadline.
export function useAvailableCheckin(userId: number | null, surveyOpen: boolean) {
  const [available, setAvailable] = useState<EMANextShowResponse | null>(null);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    let disposed = false;
    let request = 0;
    let expiryTimer: ReturnType<typeof setTimeout> | undefined;
    setAvailable(null);
    setError(false);
    if (!userId || surveyOpen) return;

    async function check() {
      const current = ++request;
      clearTimeout(expiryTimer);
      setAvailable(null);
      setError(false);
      try {
        const { data } = await ema.next(true);
        if (disposed || current !== request) return;
        // Also guard against older servers that ignore checkin_only.
        if (!data.should_show || data.ema_type === 'prompt_feedback' || !data.expires_at) return;
        const remaining = data.expires_at ? Date.parse(data.expires_at) - Date.now() : null;
        if (remaining !== null && (!Number.isFinite(remaining) || remaining <= 0)) return;
        setAvailable(data);
        if (remaining !== null) {
          expiryTimer = setTimeout(() => setAvailable(null), Math.min(remaining, 2147483647));
        }
      } catch {
        if (!disposed && current === request) setError(true);
      }
    }

    void check();
    const appState = AppState.addEventListener('change', (state) => {
      if (state === 'active') void check();
      else {
        // Discard a response that arrives after the app goes into the background.
        ++request;
        clearTimeout(expiryTimer);
        setAvailable(null);
      }
    });
    const push = Notifications.addNotificationReceivedListener((notification) => {
      if (shouldOpenEMA(parsePushData(notification.request.content.data).type)) void check();
    });
    return () => {
      disposed = true;
      clearTimeout(expiryTimer);
      appState.remove();
      push.remove();
    };
  }, [userId, surveyOpen, revision]);

  return { available, error, refresh };
}
