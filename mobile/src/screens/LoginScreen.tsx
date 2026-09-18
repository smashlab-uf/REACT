import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Keyboard,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { auth } from '../api/endpoints';
import { useAuthStore } from '../store/authStore';
import { useAlertStore } from '../store/alertStore';
import { colors, radius, typography } from '../theme';

type Props = { onGoToRegister: () => void };

export default function LoginScreen({ onGoToRegister }: Props) {
  const login = useAuthStore((s) => s.login);
  const showAlert = useAlertStore((s) => s.show);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const layoutHeight = useRef(0);
  const [contentHeight, setContentHeight] = useState<number | null>(null);

  useEffect(() => {
    const subscription = Keyboard.addListener('keyboardDidHide', () => {
      setContentHeight(null);
    });
    return () => subscription.remove();
  }, []);

  function keepLayoutBehindKeyboard() {
    // Preserve the full layout before Android's adjustResize reduces the window.
    setContentHeight((height) => height ?? (layoutHeight.current || null));
  }

  async function handleLogin() {
    if (!email || !password) {
      showAlert('Error', 'Please enter your email and password.');
      return;
    }

    setLoading(true);
    try {
      const res = await auth.login(email, password);
      const { access, refresh, data } = res.data;
      await login(access, refresh, data);
    } catch (err: any) {
      showAlert('Login failed', 'Invalid email or password.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <View
      style={styles.flex}
      onLayout={(event) => { layoutHeight.current = event.nativeEvent.layout.height; }}>
      <View style={[
        styles.container,
        contentHeight !== null && { flex: 0, height: contentHeight },
      ]}>
        <Text style={styles.title}>Welcome</Text>
        <View style={styles.titleAccent} />

        <TextInput
          style={styles.input}
          placeholder="Email"
          value={email}
          onChangeText={setEmail}
          onFocus={keepLayoutBehindKeyboard}
          onPressIn={keepLayoutBehindKeyboard}
          keyboardType="email-address"
          autoCapitalize="none"
        />
        <TextInput
          style={styles.input}
          placeholder="Password"
          value={password}
          onChangeText={setPassword}
          onFocus={keepLayoutBehindKeyboard}
          onPressIn={keepLayoutBehindKeyboard}
          secureTextEntry
        />

        <TouchableOpacity style={styles.btn} onPress={handleLogin} disabled={loading}>
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.btnText}>Log In</Text>
          )}
        </TouchableOpacity>

        <TouchableOpacity onPress={onGoToRegister} style={styles.link}>
          <Text style={styles.linkText}>Don't have an account? Register</Text>
        </TouchableOpacity>

        <View style={styles.logoArea}>
          <Image
            source={require('../../assets/images/smashlab-logo.png')}
            style={styles.logo}
            resizeMode="contain"
          />
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.surface },
  container: { flex: 1, padding: 24, paddingTop: 100 },
  logoArea: {
    flex: 1,
    minHeight: 0,
    overflow: 'hidden',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 24,
  },
  logo: {
    width: 200,
    height: 200 * (820 / 896),
    maxWidth: '60%',
    maxHeight: '100%',
  },
  title: { ...typography.screenTitle, color: colors.primary, marginBottom: 8, textAlign: 'center' },
  titleAccent: {
    width: 36,
    height: 4,
    borderRadius: radius.pill,
    backgroundColor: colors.accent,
    alignSelf: 'center',
    marginBottom: 32,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: 14,
    fontSize: 16,
    marginBottom: 14,
    color: colors.textPrimary,
  },
  btn: {
    backgroundColor: colors.primary,
    borderRadius: radius.md,
    padding: 16,
    alignItems: 'center',
    marginTop: 8,
  },
  btnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  link: { marginTop: 24, alignItems: 'center' },
  linkText: { color: colors.primary, fontSize: 15 },
});
