import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Platform, View } from 'react-native';
import * as Notifications from 'expo-notifications';
import { useAuthStore } from './src/store/authStore';
import LoginScreen from './src/screens/LoginScreen';
import RegisterScreen from './src/screens/RegisterScreen';
import ComposeScreen from './src/screens/ComposeScreen';
import EMAScreen from './src/screens/EMAScreen';
import { flushQueue, startNetworkListener } from './src/telemetry/offlineQueue';
import { registerForPushNotifications } from './src/notifications/pushToken';
import { jitai, user as userApi, telemetry } from './src/api/endpoints';
import NotificationToast from './src/components/NotificationToast';

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
          console.log('[PushToken] Failed to register with backend:', e?.response?.status);
        });
      }
    });

    const foregroundSub = Notifications.addNotificationReceivedListener((notification) => {
      const title = notification.request.content.title ?? 'Notification';
      const body = notification.request.content.body ?? '';
      const data = notification.request.content.data as Record<string, unknown>;
      console.log('[Push] Received in foreground:', JSON.stringify(data));

      const rawJitaiLogId = data.jitai_log_id;
      const jitaiLogId = typeof rawJitaiLogId === 'number'
        ? rawJitaiLogId
        : typeof rawJitaiLogId === 'string'
          ? Number(rawJitaiLogId)
          : undefined;

      if (jitaiLogId !== undefined && Number.isFinite(jitaiLogId)) {
        jitai.receipt({
          jitai_log_id: jitaiLogId,
          device_received_at: new Date().toISOString(),
          platform: Platform.OS,
          app_state: 'foreground',
        }).catch((e) => {
          console.log('[Push] Receipt log failed:', e?.response?.status ?? e?.message);
        });
      }

      showToast(`📩 ${title}${body ? ': ' + body : ''}`);
    });

    const tapSub = Notifications.addNotificationResponseReceivedListener((response) => {
      const data = response.notification.request.content.data as Record<string, unknown>;
      console.log('[Push] Tapped:', JSON.stringify(data));

      const jitaiLogId = data.jitai_log_id as number | undefined;
      telemetry.logEngagement({
        user: userId,
        event_type: 'notification_tapped',
        occurred_at: new Date().toISOString(),
        ...(jitaiLogId !== undefined && { jitai_log: jitaiLogId }),
      }).then(() => {
        showToast('✅ Engagement logged to backend');
      }).catch((e) => {
        showToast(`❌ Engagement log failed: ${e?.response?.status ?? 'Network error'}`);
        console.log('[Push] Engagement log failed:', e?.response?.status);
      });

      if (data.type === 'ema_prompt') {
        setActiveEMA({ jitaiLogId });
      }
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
