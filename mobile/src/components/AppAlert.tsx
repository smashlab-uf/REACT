import React from 'react';
import { Modal, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useAlertStore } from '../store/alertStore';
import { colors, radius, typography } from '../theme';

export default function AppAlert() {
  const { visible, title, message, hide } = useAlertStore();

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={hide}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Text style={styles.title}>{title}</Text>
          <View style={styles.titleAccent} />
          <Text style={styles.message}>{message}</Text>

          <TouchableOpacity style={styles.okBtn} onPress={hide}>
            <Text style={styles.okBtnText}>OK</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  card: {
    width: '100%',
    maxWidth: 340,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    padding: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 10,
    elevation: 6,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: colors.primary,
  },
  titleAccent: {
    width: 28,
    height: 4,
    borderRadius: radius.pill,
    backgroundColor: colors.accent,
    marginTop: 6,
    marginBottom: 12,
  },
  message: {
    ...typography.body,
    color: colors.textPrimary,
    marginBottom: 20,
  },
  okBtn: {
    backgroundColor: colors.primary,
    borderRadius: radius.md,
    paddingVertical: 14,
    alignItems: 'center',
  },
  okBtnText: {
    color: colors.onPrimary,
    fontSize: 16,
    fontWeight: '600',
  },
});
