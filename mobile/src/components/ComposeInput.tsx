import React, { useRef } from 'react';
import { StyleSheet, TextInput, TextInputProps } from 'react-native';
import { onComposeChange } from '../telemetry/composeTelemetry';

type Props = Omit<TextInputProps, 'onChangeText'> & {
  value: string;
  onChangeText: (text: string) => void;
};

export default function ComposeInput({ value, onChangeText, ...rest }: Props) {
  const prevText = useRef('');

  function handleChange(next: string) {
    onComposeChange(prevText.current, next);
    prevText.current = next;
    onChangeText(next);
  }

  return (
    <TextInput
      style={styles.input}
      value={value}
      onChangeText={handleChange}
      placeholder="What's on your mind?"
      placeholderTextColor="#999"
      multiline
      autoFocus
      autoCorrect={false}
      autoCapitalize="none"
      spellCheck={false}
      {...rest}
    />
  );
}

const styles = StyleSheet.create({
  input: {
    flex: 1,
    fontSize: 16,
    color: '#000',
    textAlignVertical: 'top',
    padding: 12,
  },
});
