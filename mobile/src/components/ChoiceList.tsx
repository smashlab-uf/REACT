import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, radius, typography } from '../theme';

type BaseProps = {
  label: string;
  options: string[];
};

type SingleProps = BaseProps & {
  multiple?: false;
  value: string | null;
  onChange: (value: string) => void;
};

type MultiProps = BaseProps & {
  multiple: true;
  value: string[];
  onChange: (value: string[]) => void;
};

export default function ChoiceList(props: SingleProps | MultiProps) {
  const selected = props.multiple ? new Set(props.value) : null;

  function handlePress(option: string) {
    if (props.multiple) {
      const next = selected?.has(option)
        ? props.value.filter((item) => item !== option)
        : [...props.value, option];
      props.onChange(next);
      return;
    }
    props.onChange(option);
  }

  return (
    <View style={styles.container}>
      <Text style={styles.label}>{props.label}</Text>
      {props.options.map((option) => {
        const isSelected = props.multiple ? !!selected?.has(option) : props.value === option;
        return (
          <TouchableOpacity
            key={option}
            style={[styles.row, isSelected && styles.rowSelected]}
            onPress={() => handlePress(option)}
            accessibilityRole={props.multiple ? 'checkbox' : 'radio'}
            accessibilityState={{ selected: isSelected, checked: isSelected }}
            accessibilityLabel={option}>
            <View style={[styles.mark, props.multiple && styles.markSquare, isSelected && styles.markSelected]} />
            <Text style={[styles.option, isSelected && styles.optionSelected]}>{option}</Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginBottom: 28 },
  label: { ...typography.label, marginBottom: 12 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingVertical: 12,
    paddingHorizontal: 12,
    marginBottom: 8,
    backgroundColor: colors.surface,
  },
  rowSelected: { borderColor: colors.accent, backgroundColor: colors.accentMuted },
  mark: {
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 1,
    borderColor: colors.border,
    marginRight: 10,
    backgroundColor: colors.surface,
  },
  markSquare: { borderRadius: 4 },
  markSelected: { backgroundColor: colors.accent, borderColor: colors.accent },
  option: { flex: 1, fontSize: 15, color: colors.textSecondary, lineHeight: 20 },
  optionSelected: { color: colors.textPrimary, fontWeight: '600' },
});
