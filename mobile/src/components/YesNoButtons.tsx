import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, radius, typography } from '../theme';

type Props = {
  label: string;
  options?: string[];
  value: string | null;
  onChange: (value: string) => void;
};

export default function YesNoButtons({
  label,
  options = ['Yes', 'No'],
  value,
  onChange,
}: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.row}>
        {options.map((option) => {
          const selected = value === option;
          return (
            <TouchableOpacity
              key={option}
              style={[styles.button, selected && styles.buttonSelected]}
              onPress={() => onChange(option)}
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              accessibilityLabel={option}>
              <Text style={[styles.buttonText, selected && styles.buttonTextSelected]}>{option}</Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginBottom: 28 },
  label: { ...typography.label, marginBottom: 12 },
  row: { flexDirection: 'row', gap: 10 },
  button: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingVertical: 14,
    alignItems: 'center',
    backgroundColor: colors.surface,
  },
  buttonSelected: { backgroundColor: colors.accent, borderColor: colors.accent },
  buttonText: { fontSize: 16, color: colors.textSecondary, fontWeight: '600' },
  buttonTextSelected: { color: '#fff' },
});
