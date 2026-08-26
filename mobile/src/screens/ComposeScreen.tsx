import React, { useState } from 'react';
import {
  Alert,
  Keyboard,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  TouchableWithoutFeedback,
  View,
} from 'react-native';
import * as Notifications from 'expo-notifications';
import ComposeInput from '../components/ComposeInput';
import { emitDraftDeleted, emitDraftSubmitted } from '../telemetry/composeTelemetry';
import { useTelemetryStore, TelemetryEvent } from '../telemetry/telemetryStore';
import { useAuthStore } from '../store/authStore';
import { jitai } from '../api/endpoints';
import { log } from '../utils/logger';

type Props = { onOpenEMA: () => void };

export default function ComposeScreen({ onOpenEMA }: Props) {
  const [text, setText] = useState('');
  const events = useTelemetryStore((s) => s.events);
  const flush = useTelemetryStore((s) => s.flush);
  const logout = useAuthStore((s) => s.logout);

  const devNote = __DEV__ ? ' Check the console for telemetry.' : '';
  const userId = useAuthStore((s) => s.userId);

  async function simulateJitaiPush() {
    if (!userId) return;
    try {
      const res = await jitai.create({
        user: userId,
        prompt_id: `PROMPT-SIM-${Date.now()}`,
        trigger_reason: 'hr_elevated+stress_high',
        hr_at_trigger: 112,
        stress_at_trigger: 78,
        send_prompt: true,
        status: 'delivered',
      });
      await Notifications.scheduleNotificationAsync({
        content: {
          title: 'Check-in',
          body: 'Tap to respond',
          data: { jitai_log_id: res.data.id, type: 'ema_prompt', prompt_id: `PROMPT-SIM-${Date.now()}` },
        },
        trigger: { type: Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL, seconds: 3 },
      });
    } catch (e: any) {
      log('[SimPush] failed:', e?.response?.status ?? e?.message);
      Alert.alert('Simulate failed', String(e?.response?.status ?? e?.message));
    }
  }

  async function simulateCheckinReminder() {
    try {
      await Notifications.scheduleNotificationAsync({
        content: {
          title: 'REACT',
          body: 'Time for your check-in.',
          data: { type: 'checkin_reminder' },
        },
        trigger: { type: Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL, seconds: 3 },
      });
    } catch (e: any) {
      log('[SimReminder] failed:', e?.message);
      Alert.alert('Simulate failed', String(e?.message ?? 'Could not schedule reminder'));
    }
  }

  function handleSubmit() {
    if (!text.trim()) {
      Alert.alert('Nothing to submit', 'Type something first.');
      return;
    }
    emitDraftSubmitted();
    setText('');
    Alert.alert('Submitted', `Your draft was submitted.${devNote}`);
  }

  function handleDelete() {
    emitDraftDeleted();
    setText('');
    Alert.alert('Deleted', `Draft cleared.${devNote}`);
  }

  return (
    <TouchableWithoutFeedback onPress={Keyboard.dismiss} accessible={false}>
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

      {__DEV__ && (
        <View style={styles.devRow}>
          <TouchableOpacity style={styles.devBtn} onPress={onOpenEMA}>
            <Text style={styles.devBtnText}>Open EMA survey (dev)</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.devBtn} onPress={simulateJitaiPush}>
            <Text style={styles.devBtnText}>Simulate JITAI push (dev)</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.devBtn} onPress={simulateCheckinReminder}>
            <Text style={styles.devBtnText}>Simulate check-in reminder (dev)</Text>
          </TouchableOpacity>
        </View>
      )}

      <ComposeInput value={text} onChangeText={setText} />

      {__DEV__ && (
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
      )}

      <View style={styles.footer}>
        <TouchableOpacity
          style={styles.logoutBtn}
          hitSlop={{ top: 8, bottom: 8, left: 12, right: 12 }}
          onPress={() => { flush(); logout(); }}
        >
          <Text style={styles.logoutBtnText}>Log out</Text>
        </TouchableOpacity>
      </View>
    </View>
    </TouchableWithoutFeedback>
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
  devRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    paddingHorizontal: 12,
    marginBottom: 4,
  },
  devBtn: {
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: '#007AFF',
  },
  devBtnText: {
    color: '#007AFF',
    fontSize: 13,
  },
  footer: {
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: '#eee',
    paddingTop: 10,
    paddingBottom: 56,
  },
  logoutBtn: {
    paddingVertical: 4,
    paddingHorizontal: 16,
  },
  logoutBtnText: {
    color: '#8a8a8e',
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
