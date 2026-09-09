import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, radius, typography } from '../theme';

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

      <View style={styles.bar}>
        {values.map((v, i) => {
          const selected = value === v;
          const isFirst = i === 0;
          const isLast = i === values.length - 1;
          return (
            <TouchableOpacity
              key={v}
              style={[
                styles.segment,
                !isLast && styles.segmentDivider,
                isFirst && styles.segmentFirst,
                isLast && styles.segmentLast,
                selected && styles.segmentSelected,
              ]}
              onPress={() => onChange(v)}
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              accessibilityLabel={`${label} ${v} of ${maxValue}`}>
              <Text style={[styles.segmentText, selected && styles.segmentTextSelected]}>{v}</Text>
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
  label: { ...typography.label, marginBottom: 12 },
  bar: {
    flexDirection: 'row',
    height: 48,
    borderRadius: radius.md,
    borderWidth: 1.5,
    borderColor: colors.border,
    overflow: 'hidden',
  },
  segment: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  segmentDivider: {
    borderRightWidth: 1,
    borderRightColor: colors.border,
  },
  segmentFirst: {
    borderTopLeftRadius: radius.md - 1,
    borderBottomLeftRadius: radius.md - 1,
  },
  segmentLast: {
    borderTopRightRadius: radius.md - 1,
    borderBottomRightRadius: radius.md - 1,
  },
  segmentSelected: { backgroundColor: colors.accent },
  segmentText: { fontSize: 15, color: colors.textSecondary },
  segmentTextSelected: { color: '#fff', fontWeight: '700' },
  anchors: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 8 },
  anchor: { fontSize: 12, color: colors.textMuted },
});
