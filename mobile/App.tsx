import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, Platform, View } from 'react-native';
import * as Notifications from 'expo-notifications';
import NetInfo from '@react-native-community/netinfo';
import { useFonts } from 'expo-font';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from './src/store/authStore';
import LoginScreen from './src/screens/LoginScreen';
import RegisterScreen from './src/screens/RegisterScreen';
import ComposeScreen from './src/screens/ComposeScreen';
import EMAScreen from './src/screens/EMAScreen';
import { flushQueue, startNetworkListener } from './src/telemetry/offlineQueue';
import { registerForPushNotifications } from './src/notifications/pushToken';
import { parsePushData, shouldOpenEMA } from './src/notifications/payload';
import { jitai, telemetry } from './src/api/endpoints';
import { pushRegistration } from './src/notifications/session';
import NotificationToast from './src/components/NotificationToast';
import AppAlert from './src/components/AppAlert';
import { log } from './src/utils/logger';
import { colors } from './src/theme';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: useAuthStore.getState().isAuthenticated,
    shouldPlaySound: useAuthStore.getState().isAuthenticated,
    shouldSetBadge: false,
    shouldShowBanner: useAuthStore.getState().isAuthenticated,
    shouldShowList: useAuthStore.getState().isAuthenticated,
  }),
});

type Screen = 'login' | 'register' | 'app';
type ActiveEMA = { jitaiLogId?: number };
type ReceiptAppState = 'foreground' | 'background' | 'killed';

const COLD_START_MAX_AGE_MS = 120000;

export default function App() {
  const [fontsLoaded] = useFonts({
    ...Ionicons.font,
  });
  const { isAuthenticated, isLoading, restoreSession, userId } = useAuthStore();
  const [screen, setScreen] = useState<Screen>('login');
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [toastVariant, setToastVariant] = useState<'info' | 'success' | 'error'>('info');
  const [activeEMA, setActiveEMA] = useState<ActiveEMA | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showToast(message: string, variant: 'info' | 'success' | 'error' = 'info') {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToastVariant(variant);
    setToastMessage(message);
    toastTimer.current = setTimeout(() => setToastMessage(null), 4000);
  }

  useEffect(() => {
    restoreSession();
    const unsubscribe = startNetworkListener();
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!isAuthenticated) return;
    flushQueue();
  }, [isAuthenticated]);

  useEffect(() => {
    if (isLoading) return;
    if (!isAuthenticated) {
      setActiveEMA(null);
      setToastMessage(null);
    }
    const retry = () => {
      const action = useAuthStore.getState().isAuthenticated
        ? pushRegistration.retry() : pushRegistration.unregister();
      action.catch(() => log('[PushToken] Cleanup still pending'));
    };
    retry();
    const unsubscribe = NetInfo.addEventListener((state) => {
      if (state.isConnected) retry();
    });
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') retry();
    });
    return () => { unsubscribe(); subscription.remove(); };
  }, [isAuthenticated, isLoading]);

  useEffect(() => {
    if (!isAuthenticated || !userId) return;
    let cancelled = false;
    const isCurrent = () => !cancelled && useAuthStore.getState().isAuthenticated
      && useAuthStore.getState().userId === userId;

    let registering = false;
    let registered = false;
    const ensureRegistered = async () => {
      if (!isCurrent() || registering || registered) return;
      registering = true;
      try {
        const token = await registerForPushNotifications();
        if (token) {
          await pushRegistration.register(userId, token, isCurrent);
          registered = isCurrent();
        }
      } catch {
        log('[PushToken] Failed to register with backend; will retry');
      } finally {
        registering = false;
      }
    };
    void ensureRegistered();
    const registrationNetworkSub = NetInfo.addEventListener((state) => {
      if (state.isConnected) void ensureRegistered();
    });
    const registrationAppSub = AppState.addEventListener('change', (state) => {
      if (state === 'active') void ensureRegistered();
    });

    const reportedReceipts = new Set<number>();
    const handledTaps = new Set<string>();

    function reportReceipt(jitaiLogId: number | undefined, appState: ReceiptAppState) {
      if (jitaiLogId === undefined || reportedReceipts.has(jitaiLogId)) return;
      reportedReceipts.add(jitaiLogId);
      jitai.receipt({
        jitai_log_id: jitaiLogId,
        device_received_at: new Date().toISOString(),
        platform: Platform.OS,
        app_state: appState,
      }).catch((e) => {
        log('[Push] Receipt log failed:', e?.response?.status ?? e?.message);
      });
    }

    function openFromPush(parsed: ReturnType<typeof parsePushData>) {
      if (!isCurrent()) return;
      if (!shouldOpenEMA(parsed.type)) return;
      setActiveEMA({ jitaiLogId: parsed.jitaiLogId });
    }

    function onTapped(response: Notifications.NotificationResponse, appState: ReceiptAppState) {
      if (!isCurrent()) return;
      const identifier = response.notification.request.identifier;
      if (handledTaps.has(identifier)) return;
      handledTaps.add(identifier);

      const data = response.notification.request.content.data as Record<string, unknown>;
      const parsed = parsePushData(data);
      log('[Push] Tapped:', JSON.stringify(data));

      reportReceipt(parsed.jitaiLogId, appState);

      telemetry.logEngagement({
        event_type: 'notification_tapped',
        occurred_at: new Date().toISOString(),
        ...(parsed.jitaiLogId !== undefined && { jitai_log: parsed.jitaiLogId }),
      }).then(() => {
        showToast('✅ Engagement logged to backend');
      }).catch((e) => {
        showToast(`❌ Engagement log failed: ${e?.response?.status ?? 'Network error'}`);
        log('[Push] Engagement log failed:', e?.response?.status);
      });

      openFromPush(parsed);
    }

    const foregroundSub = Notifications.addNotificationReceivedListener((notification) => {
      if (!isCurrent()) return;
      const content = notification.request.content;
      const data = content.data as Record<string, unknown>;
      const parsed = parsePushData(data);
      log('[Push] Received in foreground:', JSON.stringify(data));

      reportReceipt(parsed.jitaiLogId, 'foreground');

      if (parsed.type === 'checkin_reminder') {
        const title = content.title ?? 'REACT';
        const body = content.body ?? 'Time for your check-in!';
        showToast(`📩 ${title}${body ? ': ' + body : ''}`);
      }

      openFromPush(parsed);
    });

    const tapSub = Notifications.addNotificationResponseReceivedListener((response) => {
      const appState: ReceiptAppState =
        AppState.currentState === 'active' ? 'foreground' : 'background';
      onTapped(response, appState);
    });

    Notifications.getLastNotificationResponseAsync().then((response) => {
      if (!response) return;
      if (!isRecentNotification(response.notification)) return;
      onTapped(response, 'killed');
    }).catch((e) => {
      log('[Push] Last notification response failed:', e?.message);
    });

    return () => {
      cancelled = true;
      registrationNetworkSub();
      registrationAppSub.remove();
      foregroundSub.remove();
      tapSub.remove();
    };
  }, [isAuthenticated, userId]);

  if (isLoading) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={{ flex: 1 }}>
      <NotificationToast message={toastMessage} variant={toastVariant} />
      <AppAlert />
      {isAuthenticated ? (
        <ComposeScreen onOpenEMA={() => setActiveEMA({})} />
      ) : screen === 'register' ? (
        <RegisterScreen onGoToLogin={() => setScreen('login')} />
      ) : (
        <LoginScreen onGoToRegister={() => setScreen('register')} />
      )}

      <EMAScreen
        visible={isAuthenticated && activeEMA !== null}
        jitaiLogId={activeEMA?.jitaiLogId}
        onClose={() => setActiveEMA(null)}
      />
    </View>
  );
}

function isRecentNotification(notification: Notifications.Notification) {
  const raw = notification.date;
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return true;
  const ms = raw < 1e12 ? raw * 1000 : raw;
  return Date.now() - ms < COLD_START_MAX_AGE_MS;
}
