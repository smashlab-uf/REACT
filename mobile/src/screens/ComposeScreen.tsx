import React, { useState } from 'react';
import {
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import ComposeInput from '../components/ComposeInput';
import { emitDraftDeleted, emitDraftSubmitted } from '../telemetry/composeTelemetry';
import { useTelemetryStore, TelemetryEvent } from '../telemetry/telemetryStore';
import { useAuthStore } from '../store/authStore';

export default function ComposeScreen() {
  const [text, setText] = useState('');
  const events = useTelemetryStore((s) => s.events);
  const logout = useAuthStore((s) => s.logout);

  function handleSubmit() {
    if (!text.trim()) {
      Alert.alert('Nothing to submit', 'Type something first.');
      return;
    }
    emitDraftSubmitted();
    setText('');
    Alert.alert('Submitted', 'Your draft was submitted. Check the console for telemetry.');
  }

  function handleDelete() {
    emitDraftDeleted();
    setText('');
    Alert.alert('Deleted', 'Draft cleared. Check the console for telemetry.');
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.deleteBtn} onPress={handleDelete}>
          <Text style={styles.deleteBtnText}>Clear</Text>
        </TouchableOpacity>
        <Text style={styles.title}>Compose</Text>
        <TouchableOpacity style={styles.submitBtn} onPress={handleSubmit}>
          <Text style={styles.submitBtnText}>Submit</Text>
        </TouchableOpacity>
      </View>

      <TouchableOpacity style={styles.logoutBtn} onPress={logout}>
        <Text style={styles.logoutBtnText}>Logout</Text>
      </TouchableOpacity>

      <ComposeInput value={text} onChangeText={setText} />

      <View style={styles.debugPanel}>
        <Text style={styles.debugTitle}>Telemetry Events</Text>
        <ScrollView style={styles.debugScroll}>
          {events.length === 0 ? (
            <Text style={styles.debugEmpty}>No events yet. Start typing.</Text>
          ) : (
            [...events].reverse().map((e: TelemetryEvent, i) => (
              <View key={i} style={styles.debugEvent}>
                <Text style={styles.debugType}>{e.event_type}</Text>
                <Text style={styles.debugDetail}>user: {e.user ?? 'null'}</Text>
                <Text style={styles.debugDetail}>session_id: {e.session_id.slice(0, 8)}...</Text>
                <Text style={styles.debugDetail}>occurred_at: {e.occurred_at}</Text>
                <Text style={styles.debugDetail}>screen: {e.screen_name}</Text>
                <Text style={styles.debugDetail}>keystrokes: {(e.metadata.keystroke_count as number)}</Text>
                <Text style={styles.debugDetail}>deletes: {(e.metadata.delete_count as number)}</Text>
                <Text style={styles.debugDetail}>time: {(e.metadata.time_on_compose as number)}ms</Text>
              </View>
            ))
          )}
        </ScrollView>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#fff',
    paddingTop: 60,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#eee',
  },
  title: {
    fontSize: 17,
    fontWeight: '600',
  },
  deleteBtn: {
    padding: 8,
  },
  deleteBtnText: {
    fontSize: 15,
    color: '#e00',
  },
  submitBtn: {
    backgroundColor: '#007AFF',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
  },
  submitBtnText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 15,
  },
  logoutBtn: {
    alignSelf: 'center',
    paddingVertical: 6,
    paddingHorizontal: 20,
    marginBottom: 4,
  },
  logoutBtnText: {
    color: '#e00',
    fontSize: 13,
  },
  debugPanel: {
    height: 200,
    borderTopWidth: 1,
    borderTopColor: '#ddd',
    backgroundColor: '#f9f9f9',
    padding: 10,
  },
  debugTitle: {
    fontSize: 12,
    fontWeight: '700',
    color: '#555',
    marginBottom: 6,
    textTransform: 'uppercase',
  },
  debugScroll: {
    flex: 1,
  },
  debugEmpty: {
    fontSize: 12,
    color: '#aaa',
    fontStyle: 'italic',
  },
  debugEvent: {
    backgroundColor: '#fff',
    borderRadius: 6,
    padding: 8,
    marginBottom: 6,
    borderLeftWidth: 3,
    borderLeftColor: '#007AFF',
  },
  debugType: {
    fontSize: 13,
    fontWeight: '700',
    color: '#007AFF',
    marginBottom: 2,
  },
  debugDetail: {
    fontSize: 12,
    color: '#444',
  },
});
