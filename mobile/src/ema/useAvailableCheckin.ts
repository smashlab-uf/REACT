import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import NetInfo from '@react-native-community/netinfo';
import * as Notifications from 'expo-notifications';
import { ema } from '../api/endpoints';
import { EMANextShowResponse, EMASubmitResponse, ResourceCard, ResourceCardState } from '../api/types';
import { parsePushData, shouldOpenEMA } from '../notifications/payload';

const REFRESH_MS = 60_000;
const MAX_TIMEOUT_MS = 2147483647;

type SupportState = { userId: number; card: ResourceCard; expiresAt: string | null };

// Availability reads never extend the server's notification-based deadline.
export function useAvailableCheckin(userId: number | null, surveyOpen: boolean) {
  const [available, setAvailable] = useState<EMANextShowResponse | null>(null);
  const [support, setSupport] = useState<SupportState | null>(null);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const request = useRef(0);
  const currentUserId = useRef(userId);
  currentUserId.current = userId;
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  const updateSupport = useCallback((data: ResourceCardState) => {
    if (!userId || currentUserId.current !== userId) return;
    if (data.distress_active === false || data.resource_card === null) {
      setSupport(null);
    } else if (data.resource_card) {
      setSupport({ userId, card: data.resource_card, expiresAt: data.distress_expires_at ?? null });
    }
    // An older server may omit resource state. Do not erase a confirmed card.
  }, [userId]);

  const onSubmitted = useCallback((data: EMASubmitResponse) => {
    if (data.user !== userId || currentUserId.current !== userId) return;
    // A pre-submission request must not overwrite the newly raised flag.
    ++request.current;
    setAvailable(null);
    updateSupport(data);
  }, [userId, updateSupport]);

  useEffect(() => { setSupport(null); }, [userId]);

  useEffect(() => {
    if (!support || support.userId !== userId || !support.expiresAt) return;
    const remaining = Date.parse(support.expiresAt) - Date.now();
    if (!Number.isFinite(remaining)) return;
    // The backend returns the latest expiry across all active momentary flags.
    const timer = setTimeout(() => {
      if (remaining <= MAX_TIMEOUT_MS) setSupport((current) => current === support ? null : current);
      refresh();
    }, Math.max(0, Math.min(remaining, MAX_TIMEOUT_MS)));
    return () => clearTimeout(timer);
  }, [support, userId, refresh]);

  useEffect(() => {
    let disposed = false;
    let expiryTimer: ReturnType<typeof setTimeout> | undefined;
    setAvailable(null);
    setError(false);
    if (!userId || surveyOpen) return;

    async function check() {
      if (AppState.currentState !== 'active') return;
      const current = ++request.current;
      setError(false);
      try {
        const { data } = await ema.next(true);
        if (disposed || current !== request.current || currentUserId.current !== userId) return;
        // Resource availability is independent of whether a check-in is due.
        updateSupport(data);
        clearTimeout(expiryTimer);
        setAvailable(null);
        // Also guard against older servers that ignore checkin_only.
        if (!data.should_show || data.ema_type === 'prompt_feedback' || !data.expires_at) return;
        const remaining = Date.parse(data.expires_at) - Date.now();
        if (!Number.isFinite(remaining) || remaining <= 0) return;
        setAvailable(data);
        expiryTimer = setTimeout(() => setAvailable(null), Math.min(remaining, MAX_TIMEOUT_MS));
      } catch {
        // Keep confirmed resources during a transient network failure.
        if (!disposed && current === request.current) setError(true);
      }
    }

    void check();
    // Baseline flags can be cleared by staff while Home remains open.
    const poll = setInterval(() => { void check(); }, REFRESH_MS);
    const network = NetInfo.addEventListener((state) => {
      if (state.isConnected) void check();
    });
    const appState = AppState.addEventListener('change', (state) => {
      if (state === 'active') void check();
      else {
        // Discard a response that arrives after the app goes into the background.
        ++request.current;
        clearTimeout(expiryTimer);
        setAvailable(null);
      }
    });
    const push = Notifications.addNotificationReceivedListener((notification) => {
      if (shouldOpenEMA(parsePushData(notification.request.content.data).type)) void check();
    });
    return () => {
      disposed = true;
      ++request.current;
      clearTimeout(expiryTimer);
      clearInterval(poll);
      network();
      appState.remove();
      push.remove();
    };
  }, [userId, surveyOpen, revision, updateSupport]);

  const resourceCard = support?.userId === userId
    && (!support.expiresAt || Date.parse(support.expiresAt) > Date.now()) ? support.card : null;
  return { available, resourceCard, error, refresh, onSubmitted };
}
