import React, { useEffect, useState } from 'react';
import { ActivityIndicator, View } from 'react-native';
import { useAuthStore } from './src/store/authStore';
import LoginScreen from './src/screens/LoginScreen';
import RegisterScreen from './src/screens/RegisterScreen';
import ComposeScreen from './src/screens/ComposeScreen';

type Screen = 'login' | 'register' | 'app';

export default function App() {
  const { isAuthenticated, isLoading, restoreSession } = useAuthStore();
  const [screen, setScreen] = useState<Screen>('login');

  useEffect(() => {
    restoreSession();
  }, []);

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
