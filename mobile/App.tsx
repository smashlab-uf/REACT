import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, Platform, View } from 'react-native';
import * as Notifications from 'expo-notifications';
import { useAuthStore } from './src/store/authStore';
import LoginScreen from './src/screens/LoginScreen';
import RegisterScreen from './src/screens/RegisterScreen';
import ComposeScreen from './src/screens/ComposeScreen';
import EMAScreen from './src/screens/EMAScreen';
import { flushQueue, startNetworkListener } from './src/telemetry/offlineQueue';
import { registerForPushNotifications } from './src/notifications/pushToken';
import { parsePushData, shouldOpenEMA } from './src/notifications/payload';
import { jitai, user as userApi, telemetry } from './src/api/endpoints';
import NotificationToast from './src/components/NotificationToast';
import { log } from './src/utils/logger';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

type Screen = 'login' | 'register' | 'app';
type ActiveEMA = { jitaiLogId?: number };
type ReceiptAppState = 'foreground' | 'background' | 'killed';

const COLD_START_MAX_AGE_MS = 120000;

export default function App() {
  const { isAuthenticated, isLoading, restoreSession, userId } = useAuthStore();
  const [screen, setScreen] = useState<Screen>('login');
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [activeEMA, setActiveEMA] = useState<ActiveEMA | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showToast(message: string) {
    if (toastTimer.current) clearTimeout(toastTimer.current);
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
    if (!isAuthenticated || !userId) return;

    registerForPushNotifications().then((token) => {
      if (token) {
        userApi.update(userId, { push_token: token }).catch((e) => {
          log('[PushToken] Failed to register with backend:', e?.response?.status);
        });
      }
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
      if (!shouldOpenEMA(parsed.type)) return;
      setActiveEMA({ jitaiLogId: parsed.jitaiLogId });
    }

    function onTapped(response: Notifications.NotificationResponse, appState: ReceiptAppState) {
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
      const content = notification.request.content;
      const data = content.data as Record<string, unknown>;
      const parsed = parsePushData(data);
      log('[Push] Received in foreground:', JSON.stringify(data));

      reportReceipt(parsed.jitaiLogId, 'foreground');

      if (parsed.type === 'checkin_reminder') {
        const title = content.title ?? 'REACT';
        const body = content.body ?? 'Time for your check-in.';
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
      foregroundSub.remove();
      tapSub.remove();
    };
  }, [isAuthenticated, userId]);

  if (isLoading) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator size="large" color="#007AFF" />
      </View>
    );
  }

  return (
    <View style={{ flex: 1 }}>
      <NotificationToast message={toastMessage} />
      {isAuthenticated ? (
        <ComposeScreen onOpenEMA={() => setActiveEMA({})} />
      ) : screen === 'register' ? (
        <RegisterScreen onGoToLogin={() => setScreen('login')} />
      ) : (
        <LoginScreen onGoToRegister={() => setScreen('register')} />
      )}

      <EMAScreen
        visible={activeEMA !== null}
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
