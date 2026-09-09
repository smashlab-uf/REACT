import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, typography } from '../theme';

type Props = {
  label: string;
  minValue?: number;
  maxValue?: number;
  value: number | null;
  onChange: (value: number) => void;
};

export default function NumberStepper({
  label,
  minValue = 0,
  maxValue,
  value,
  onChange,
}: Props) {
  const atMin = value !== null && value <= minValue;
  const atMax = value !== null && maxValue !== undefined && value >= maxValue;

  function decrement() {
    if (value === null) {
      onChange(minValue);
      return;
    }
    if (value > minValue) onChange(value - 1);
  }

  function increment() {
    if (value === null) {
      onChange(minValue + 1);
      return;
    }
    if (maxValue === undefined || value < maxValue) onChange(value + 1);
  }

  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.row}>
        <TouchableOpacity
          style={[styles.step, atMin && styles.stepDisabled]}
          onPress={decrement}
          disabled={atMin}
          accessibilityRole="button"
          accessibilityLabel="Decrease">
          <Text style={[styles.stepText, atMin && styles.stepTextDisabled]}>−</Text>
        </TouchableOpacity>
        <Text style={[styles.value, value === null && styles.valueEmpty]}>
          {value === null ? '—' : String(value)}
        </Text>
        <TouchableOpacity
          style={[styles.step, atMax && styles.stepDisabled]}
          onPress={increment}
          disabled={atMax}
          accessibilityRole="button"
          accessibilityLabel="Increase">
          <Text style={[styles.stepText, atMax && styles.stepTextDisabled]}>+</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginBottom: 28 },
  label: { ...typography.label, marginBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center' },
  step: {
    width: 48,
    height: 48,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  stepDisabled: { borderColor: colors.border },
  stepText: { fontSize: 28, color: colors.primary, lineHeight: 32, marginTop: -2 },
  stepTextDisabled: { color: colors.disabled },
  value: {
    minWidth: 64,
    textAlign: 'center',
    fontSize: 28,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  valueEmpty: { color: colors.textMuted },
});
