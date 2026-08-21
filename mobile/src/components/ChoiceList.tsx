import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

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
  label: { fontSize: 16, fontWeight: '600', color: '#111', marginBottom: 12 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#ccc',
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 12,
    marginBottom: 8,
    backgroundColor: '#fff',
  },
  rowSelected: { borderColor: '#007AFF', backgroundColor: '#f0f6ff' },
  mark: {
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 1,
    borderColor: '#ccc',
    marginRight: 10,
    backgroundColor: '#fff',
  },
  markSquare: { borderRadius: 4 },
  markSelected: { backgroundColor: '#007AFF', borderColor: '#007AFF' },
  option: { flex: 1, fontSize: 15, color: '#444', lineHeight: 20 },
  optionSelected: { color: '#111', fontWeight: '600' },
});
