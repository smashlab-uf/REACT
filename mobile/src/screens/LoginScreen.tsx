import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { auth } from '../api/endpoints';
import { useAuthStore } from '../store/authStore';
import { colors, radius, typography } from '../theme';

type Props = { onGoToRegister: () => void };

export default function LoginScreen({ onGoToRegister }: Props) {
  const login = useAuthStore((s) => s.login);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleLogin() {
    if (!email || !password) {
      Alert.alert('Error', 'Please enter your email and password.');
      return;
    }

    setLoading(true);
    try {
      const res = await auth.login(email, password);
      const { access, refresh, data } = res.data;
      await login(access, refresh, data);
    } catch (err: any) {
      Alert.alert('Login failed', 'Invalid email or password.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={styles.container}>
        <Text style={styles.title}>Welcome</Text>
        <View style={styles.titleAccent} />

        <TextInput
          style={styles.input}
          placeholder="Email"
          value={email}
          onChangeText={setEmail}
          keyboardType="email-address"
          autoCapitalize="none"
        />
        <TextInput
          style={styles.input}
          placeholder="Password"
          value={password}
          onChangeText={setPassword}
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
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.surface },
  container: { flex: 1, padding: 24, paddingTop: 100 },
  title: { ...typography.screenTitle, color: colors.primary, marginBottom: 8 },
  titleAccent: { width: 36, height: 4, borderRadius: radius.pill, backgroundColor: colors.accent, marginBottom: 32 },
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
