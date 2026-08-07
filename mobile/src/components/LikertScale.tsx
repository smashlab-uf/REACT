import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

const VALUES = [1, 2, 3, 4, 5, 6, 7];

type Props = {
  label: string;
  lowLabel: string;
  highLabel: string;
  value: number | null;
  onChange: (value: number) => void;
};

export default function LikertScale({ label, lowLabel, highLabel, value, onChange }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>

      <View style={styles.row}>
        {VALUES.map((v) => {
          const selected = value === v;
          return (
            <TouchableOpacity
              key={v}
              style={[styles.dot, selected && styles.dotSelected]}
              onPress={() => onChange(v)}
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              accessibilityLabel={`${label} ${v} of 7`}>
              <Text style={[styles.dotText, selected && styles.dotTextSelected]}>{v}</Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <View style={styles.anchors}>
        <Text style={styles.anchor}>{lowLabel}</Text>
        <Text style={styles.anchor}>{highLabel}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginBottom: 28 },
  label: { fontSize: 16, fontWeight: '600', color: '#111', marginBottom: 12 },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  dot: {
    width: 40,
    height: 40,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#ccc',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  dotSelected: { backgroundColor: '#007AFF', borderColor: '#007AFF' },
  dotText: { fontSize: 16, color: '#444' },
  dotTextSelected: { color: '#fff', fontWeight: '700' },
  anchors: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
  anchor: { fontSize: 12, color: '#888' },
});
