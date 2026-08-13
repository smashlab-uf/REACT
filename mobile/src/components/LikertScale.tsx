import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

type Props = {
  label: string;
  minValue?: number;
  maxValue?: number;
  lowLabel?: string;
  highLabel?: string;
  value: number | null;
  onChange: (value: number) => void;
};

function range(min: number, max: number) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) return [];
  return Array.from({ length: max - min + 1 }, (_, i) => min + i);
}

export default function LikertScale({
  label,
  minValue = 1,
  maxValue = 7,
  lowLabel,
  highLabel,
  value,
  onChange,
}: Props) {
  const values = range(minValue, maxValue);

  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>

      <View style={styles.row}>
        {values.map((v) => {
          const selected = value === v;
          return (
            <TouchableOpacity
              key={v}
              style={[styles.dot, selected && styles.dotSelected]}
              onPress={() => onChange(v)}
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              accessibilityLabel={`${label} ${v} of ${maxValue}`}>
              <Text style={[styles.dotText, selected && styles.dotTextSelected]}>{v}</Text>
            </TouchableOpacity>
          );
        })}
      </View>

      {(lowLabel || highLabel) && (
        <View style={styles.anchors}>
          <Text style={styles.anchor}>{lowLabel ?? ''}</Text>
          <Text style={styles.anchor}>{highLabel ?? ''}</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginBottom: 28 },
  label: { fontSize: 16, fontWeight: '600', color: '#111', marginBottom: 12 },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  dot: {
    flex: 1,
    aspectRatio: 1,
    maxWidth: 44,
    marginHorizontal: 2,
    borderRadius: 22,
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
