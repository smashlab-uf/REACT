import React, { useEffect, useState } from 'react';
import { ActivityIndicator, View } from 'react-native';
import * as Notifications from 'expo-notifications';
import { useAuthStore } from './src/store/authStore';
import LoginScreen from './src/screens/LoginScreen';
import RegisterScreen from './src/screens/RegisterScreen';
import ComposeScreen from './src/screens/ComposeScreen';
import { flushQueue, startNetworkListener } from './src/telemetry/offlineQueue';
import { registerForPushNotifications } from './src/notifications/pushToken';
import { user as userApi, telemetry } from './src/api/endpoints';

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

export default function App() {
  const { isAuthenticated, isLoading, restoreSession, userId } = useAuthStore();
  const [screen, setScreen] = useState<Screen>('login');

  useEffect(() => {
    restoreSession();
    flushQueue();
    const unsubscribe = startNetworkListener();
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!isAuthenticated || !userId) return;

    registerForPushNotifications().then((token) => {
      if (token) {
        userApi.update(userId, { push_token: token }).catch((e) => {
          console.log('[PushToken] Failed to register with backend:', e?.response?.status);
        });
      }
    });

    // Foreground notification listener
    const foregroundSub = Notifications.addNotificationReceivedListener((notification) => {
      console.log('[Push] Received in foreground:', JSON.stringify(notification.request.content.data));
    });

    // Notification tap listener
    const tapSub = Notifications.addNotificationResponseReceivedListener((response) => {
      const data = response.notification.request.content.data as Record<string, unknown>;
      console.log('[Push] Tapped:', JSON.stringify(data));

      const jitaiLogId = data.jitai_log_id as number | undefined;
      telemetry.logEngagement({
        user: userId,
        event_type: 'notification_tapped',
        occurred_at: new Date().toISOString(),
        ...(jitaiLogId !== undefined && { jitai_log: jitaiLogId }),
      }).catch((e) => {
        console.log('[Push] Engagement log failed:', e?.response?.status);
      });
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

  if (isAuthenticated) {
    return <ComposeScreen />;
  }

  if (screen === 'register') {
    return <RegisterScreen onGoToLogin={() => setScreen('login')} />;
  }

  return <LoginScreen onGoToRegister={() => setScreen('register')} />;
}
