import React, { useEffect, useRef } from 'react';
import { Animated, StyleSheet, Text } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, radius } from '../theme';

type Variant = 'info' | 'success' | 'error';

type Props = {
  message: string | null;
  variant?: Variant;
};

const ICONS: Record<Variant, keyof typeof Ionicons.glyphMap> = {
  info: 'notifications',
  success: 'checkmark-circle',
  error: 'alert-circle',
};

const ACCENTS: Record<Variant, string> = {
  info: colors.primary,
  success: colors.success,
  error: colors.danger,
};

export default function NotificationToast({ message, variant = 'info' }: Props) {
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (!message) return;

    Animated.sequence([
      Animated.timing(opacity, { toValue: 1, duration: 300, useNativeDriver: true }),
      Animated.delay(3000),
      Animated.timing(opacity, { toValue: 0, duration: 300, useNativeDriver: true }),
    ]).start();
  }, [message]);

  if (!message) return null;

  return (
    <Animated.View style={[styles.toast, { opacity, borderLeftColor: ACCENTS[variant] }]}>
      <Ionicons name={ICONS[variant]} size={20} color={ACCENTS[variant]} style={styles.icon} />
      <Text style={styles.text}>{message}</Text>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  toast: {
    position: 'absolute',
    top: 60,
    left: 20,
    right: 20,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.toastBackground,
    borderRadius: radius.md,
    borderLeftWidth: 4,
    padding: 14,
    zIndex: 999,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 10,
    elevation: 6,
  },
  icon: {
    marginRight: 10,
  },
  text: {
    flex: 1,
    color: colors.toastText,
    fontSize: 14,
    lineHeight: 19,
  },
});
