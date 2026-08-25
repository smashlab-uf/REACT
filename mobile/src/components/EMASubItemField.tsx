import React from 'react';
import { StyleSheet, Text } from 'react-native';
import { EMAAnswerValue, EMASubItem } from '../api/types';
import ChoiceList from './ChoiceList';
import LikertScale from './LikertScale';
import NumberStepper from './NumberStepper';
import YesNoButtons from './YesNoButtons';

type Props = {
  sub: EMASubItem;
  value: EMAAnswerValue | undefined;
  onChange: (value: EMAAnswerValue) => void;
};

export default function EMASubItemField({ sub, value, onChange }: Props) {
  switch (sub.response_type) {
    case 'likert':
      return (
        <LikertScale
          label={sub.text}
          minValue={sub.min_value ?? 1}
          maxValue={sub.max_value ?? 7}
          lowLabel={sub.low_label}
          highLabel={sub.high_label}
          value={typeof value === 'number' ? value : null}
          onChange={onChange}
        />
      );
    case 'single_choice':
      return (
        <ChoiceList
          label={sub.text}
          options={sub.choices ?? []}
          value={typeof value === 'string' ? value : null}
          onChange={onChange}
        />
      );
    case 'multi_choice':
      return (
        <ChoiceList
          multiple
          label={sub.text}
          options={sub.choices ?? []}
          value={Array.isArray(value) ? value : []}
          onChange={onChange}
        />
      );
    case 'yes_no':
      return (
        <YesNoButtons
          label={sub.text}
          options={sub.choices ?? ['Yes', 'No']}
          value={typeof value === 'string' ? value : null}
          onChange={onChange}
        />
      );
    case 'number':
      return (
        <NumberStepper
          label={sub.text}
          minValue={sub.min_value ?? 0}
          maxValue={sub.max_value}
          value={typeof value === 'number' ? value : null}
          onChange={onChange}
        />
      );
    default:
      return <Text style={styles.unsupported}>This question could not be displayed. Please update the app.</Text>;
  }
}

const styles = StyleSheet.create({
  unsupported: { fontSize: 13, color: '#a15c00', marginBottom: 16 },
});
