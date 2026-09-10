import React, { useRef } from 'react';
import { StyleSheet, TextInput, TextInputProps, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { onComposeChange } from '../telemetry/composeTelemetry';
import { colors, radius } from '../theme';

type Props = Omit<TextInputProps, 'onChangeText'> & {
  value: string;
  onChangeText: (text: string) => void;
  onSubmit: () => void;
};

const MIN_INPUT_HEIGHT = 44;

export default function ComposeInput({ value, onChangeText, onSubmit, ...rest }: Props) {
  const prevText = useRef('');
  const isEmpty = value.trim().length === 0;

  function handleChange(next: string) {
    onComposeChange(prevText.current, next);
    prevText.current = next;
    onChangeText(next);
  }

  return (
    <View style={styles.container}>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={handleChange}
        placeholder="What's on your mind?"
        placeholderTextColor="#999"
        multiline
        scrollEnabled={false}
        autoFocus
        autoCorrect={false}
        autoCapitalize="none"
        spellCheck={false}
        {...rest}
      />
      <View style={styles.actionRow}>
        <TouchableOpacity
          style={[styles.submitBtn, isEmpty && styles.submitBtnDisabled]}
          onPress={onSubmit}
          disabled={isEmpty}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Ionicons name="arrow-forward" size={18} color="#fff" />
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginHorizontal: 16,
    marginVertical: 12,
    borderWidth: 1,
    borderColor: colors.primaryBorder,
    borderRadius: radius.md,
    backgroundColor: '#fbfbfd',
  },
  input: {
    minHeight: MIN_INPUT_HEIGHT,
    fontSize: 16,
    color: '#000',
    textAlignVertical: 'top',
    padding: 12,
  },
  actionRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    paddingHorizontal: 8,
    paddingBottom: 8,
  },
  submitBtn: {
    width: 32,
    height: 32,
    borderRadius: radius.sm,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
  },
  submitBtnDisabled: {
    backgroundColor: colors.primaryBorder,
  },
});
