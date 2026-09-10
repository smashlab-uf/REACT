import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { auth } from '../api/endpoints';
import { useAuthStore } from '../store/authStore';
import { colors, radius, typography } from '../theme';

type Props = { onGoToLogin: () => void };

export default function RegisterScreen({ onGoToLogin }: Props) {
  const login = useAuthStore((s) => s.login);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleRegister() {
    if (!email || !password) {
      Alert.alert('Error', 'Email and password are required.');
      return;
    }
    if (password.length < 8) {
      Alert.alert('Error', 'Password must be at least 8 characters.');
      return;
    }

    setLoading(true);
    try {
      // Check email availability first
      const checkRes = await auth.checkEmail(email);
      if (checkRes.data.exists) {
        Alert.alert('Email taken', 'An account with this email already exists.');
        return;
      }

      // Register
      await auth.register({ email, password, first_name: firstName, last_name: lastName });

      // Auto-login after registration
      const loginRes = await auth.login(email, password);
      const { access, refresh, data } = loginRes.data;
      await login(access, refresh, data);
    } catch (err: any) {
      const msg = err.response?.data
        ? JSON.stringify(err.response.data)
        : 'Registration failed. Please try again.';
      Alert.alert('Error', msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Create Account</Text>
        <View style={styles.titleAccent} />

        <TextInput
          style={styles.input}
          placeholder="First name"
          value={firstName}
          onChangeText={setFirstName}
          autoCapitalize="words"
        />
        <TextInput
          style={styles.input}
          placeholder="Last name"
          value={lastName}
          onChangeText={setLastName}
          autoCapitalize="words"
        />
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
          placeholder="Password (min 8 characters)"
          value={password}
          onChangeText={setPassword}
          secureTextEntry
        />

        <TouchableOpacity style={styles.btn} onPress={handleRegister} disabled={loading}>
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.btnText}>Create Account</Text>
          )}
        </TouchableOpacity>

        <TouchableOpacity onPress={onGoToLogin} style={styles.link}>
          <Text style={styles.linkText}>Already have an account? Log in</Text>
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.surface },
  container: { padding: 24, paddingTop: 80 },
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
