import React, { useEffect } from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { ResourceCard } from '../api/types';
import { colors, radius, typography } from '../theme';

const DIALABLE = /^[0-9+\-\s()]+$/;

// Both screens render the server's resource wording without rewriting it.
// onShown fires once per mount, so each time the card is put on screen is logged once.
export default function SupportResourceCard({ card, onShown }: { card: ResourceCard; onShown?: () => void }) {
  useEffect(() => {
    onShown?.();
  }, []);

  return (
    <View>
      <Text accessibilityRole="header" style={styles.title}>{card.title}</Text>
      <Text style={styles.message}>{card.message}</Text>
      {card.resources.map((resource) => (
        <View key={resource.name} style={styles.resource}>
          <Text style={styles.name}>{resource.name}</Text>
          {resource.description ? <Text style={styles.description}>{resource.description}</Text> : null}
          {resource.contact && DIALABLE.test(resource.contact) ? (
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={`Call ${resource.name}`}
              style={styles.call}
              onPress={() => Linking.openURL(`tel:${resource.contact!.replace(/\s/g, '')}`)}>
              <Text style={styles.callText}>Call {resource.contact}</Text>
            </TouchableOpacity>
          ) : resource.contact ? <Text style={styles.contact}>{resource.contact}</Text> : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  title: { ...typography.heading, marginBottom: 20 },
  message: { ...typography.body, lineHeight: 21, marginBottom: 20 },
  resource: { backgroundColor: colors.surfaceMuted, borderRadius: radius.md, padding: 16, marginBottom: 12 },
  name: { ...typography.label, marginBottom: 4 },
  description: { ...typography.body, textAlign: 'center', lineHeight: 21 },
  contact: { ...typography.body, marginTop: 8, fontWeight: '600' },
  call: { backgroundColor: colors.primary, borderRadius: radius.md, padding: 12, alignItems: 'center', marginTop: 12 },
  callText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
