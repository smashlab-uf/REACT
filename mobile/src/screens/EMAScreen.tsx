import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import LikertScale from '../components/LikertScale';
import { ema as emaApi, telemetry } from '../api/endpoints';
import { useAuthStore } from '../store/authStore';

const PLACEHOLDER_COPY_PENDING_PI_SIGNOFF = {
  title: 'How are you feeling right now?',
  mood: { label: 'Mood', low: 'low', high: 'high' },
  stress: { label: 'Stress', low: 'none', high: 'a lot' },
  energy: { label: 'Energy', low: 'drained', high: 'energized' },
};

type Props = {
  visible: boolean;
  promptId: string;
  jitaiLogId?: number;
  onClose: () => void;
};

export default function EMAScreen({ visible, promptId, jitaiLogId, onClose }: Props) {
  const userId = useAuthStore((s) => s.userId);

  const [mood, setMood] = useState<number | null>(null);
  const [stress, setStress] = useState<number | null>(null);
  const [energy, setEnergy] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const copy = PLACEHOLDER_COPY_PENDING_PI_SIGNOFF;
  const complete = mood !== null && stress !== null && energy !== null;

  function logEngagement(event_type: 'ema_opened' | 'ema_dismissed' | 'ema_completed') {
    if (!userId) return;
    telemetry.logEngagement({
      user: userId,
      event_type,
      occurred_at: new Date().toISOString(),
      ...(jitaiLogId !== undefined && { jitai_log: jitaiLogId }),
    }).catch((e) => {
      console.log(`[EMA] ${event_type} log failed:`, e?.response?.status ?? e?.message);
    });
  }

  useEffect(() => {
    if (!visible) return;
    setMood(null);
    setStress(null);
    setEnergy(null);
    setSubmitting(false);
    logEngagement('ema_opened');
  }, [visible, promptId]);

  function handleDismiss() {
    if (submitting) return;
    logEngagement('ema_dismissed');
    onClose();
  }

  async function handleSubmit() {
    if (!complete || !userId || submitting) return;
    setSubmitting(true);
    try {
      await emaApi.submit({
        user: userId,
        prompt_id: promptId,
        mood: mood as number,
        stress: stress as number,
        energy: energy as number,
      });
      logEngagement('ema_completed');
      onClose();
    } catch (e: any) {
      console.log('[EMA] submit failed:', e?.response?.status, e?.response?.data);
      Alert.alert('Could not submit', 'Check your connection and try again.');
      setSubmitting(false);
    }
  }

  return (
    <Modal visible={visible} animationType="slide" transparent={false} onRequestClose={handleDismiss}>
      <View style={styles.container}>
        <View style={styles.header}>
          <TouchableOpacity onPress={handleDismiss} style={styles.close} accessibilityLabel="Dismiss survey">
            <Text style={styles.closeText}>✕</Text>
          </TouchableOpacity>
        </View>

        <ScrollView contentContainerStyle={styles.body}>
          <Text style={styles.title}>{copy.title}</Text>

          <LikertScale
            label={copy.mood.label}
            lowLabel={copy.mood.low}
            highLabel={copy.mood.high}
            value={mood}
            onChange={setMood}
          />
          <LikertScale
            label={copy.stress.label}
            lowLabel={copy.stress.low}
            highLabel={copy.stress.high}
            value={stress}
            onChange={setStress}
          />
          <LikertScale
            label={copy.energy.label}
            lowLabel={copy.energy.low}
            highLabel={copy.energy.high}
            value={energy}
            onChange={setEnergy}
          />
        </ScrollView>

        <View style={styles.footer}>
          <TouchableOpacity
            style={[styles.submit, !complete && styles.submitDisabled]}
            onPress={handleSubmit}
            disabled={!complete || submitting}>
            {submitting ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.submitText}>Submit</Text>
            )}
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff', paddingTop: 60 },
  header: { flexDirection: 'row', justifyContent: 'flex-end', paddingHorizontal: 16 },
  close: { padding: 8 },
  closeText: { fontSize: 20, color: '#888' },
  body: { paddingHorizontal: 24, paddingTop: 12, paddingBottom: 24 },
  title: { fontSize: 22, fontWeight: '700', color: '#111', marginBottom: 32 },
  footer: { paddingHorizontal: 24, paddingBottom: 40, paddingTop: 8 },
  submit: {
    backgroundColor: '#007AFF',
    borderRadius: 10,
    padding: 16,
    alignItems: 'center',
  },
  submitDisabled: { backgroundColor: '#b8b8b8' },
  submitText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
