import React, { useCallback, useEffect, useState } from 'react';
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
import EMASubItemField from '../components/EMASubItemField';
import { ema as emaApi, telemetry } from '../api/endpoints';
import { EMAAnswerValue, EMAItem, EMANextShowResponse, EMASubItem } from '../api/types';
import { isAnswered, isSubItemVisible, pruneHiddenAnswers, visibleSubItems } from '../ema/visibility';
import { useAuthStore } from '../store/authStore';
import { log } from '../utils/logger';

const NO_SHOW_COPY: Record<string, string> = {
  daily_cap_reached: 'You have completed all of your check-ins for today.',
  outcome_window_already_completed: 'You have already completed this check-in.',
};

const NO_SHOW_FALLBACK = 'There is no check-in for you right now.';

const MAX_TIMEOUT_MS = 2147483647;

type Phase = 'loading' | 'form' | 'noshow' | 'expired' | 'error';

type Props = {
  visible: boolean;
  jitaiLogId?: number;
  onClose: () => void;
};

function isSupported(sub: EMASubItem) {
  return (
    sub.response_type === 'likert' ||
    sub.response_type === 'single_choice' ||
    sub.response_type === 'multi_choice' ||
    sub.response_type === 'yes_no' ||
    sub.response_type === 'number'
  );
}

export default function EMAScreen({ visible, jitaiLogId, onClose }: Props) {
  const userId = useAuthStore((s) => s.userId);

  const [phase, setPhase] = useState<Phase>('loading');
  const [survey, setSurvey] = useState<EMANextShowResponse | null>(null);
  const [noShowReason, setNoShowReason] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, EMAAnswerValue>>({});
  const [submitting, setSubmitting] = useState(false);

  const items = survey?.items ?? [];
  const visibleItems = visibleSubItems(items, answers).filter(isSupported);
  const complete =
    visibleItems.length > 0 && visibleItems.every((sub) => isAnswered(answers[sub.sub_item_id]));

  const logEngagement = useCallback(
    (event_type: 'ema_opened' | 'ema_dismissed' | 'ema_completed', logId?: number | null) => {
      if (!userId) return;
      const linked = logId ?? jitaiLogId;
      telemetry
        .logEngagement({
          user: userId,
          event_type,
          occurred_at: new Date().toISOString(),
          ...(linked !== undefined && linked !== null && { jitai_log: linked }),
        })
        .catch((e) => {
          log(`[EMA] ${event_type} log failed:`, e?.response?.status ?? e?.message);
        });
    },
    [userId, jitaiLogId],
  );

  const load = useCallback(async () => {
    setPhase('loading');
    setSurvey(null);
    setNoShowReason(null);
    setAnswers({});
    setSubmitting(false);

    try {
      const { data } = await emaApi.next();

      if (!data.should_show) {
        setNoShowReason(data.reason ?? null);
        setPhase('noshow');
        return;
      }

      setSurvey(data);

      const remaining = msUntil(data.expires_at);
      if (remaining !== null && remaining <= 0) {
        setPhase('expired');
        return;
      }

      setPhase('form');
      logEngagement('ema_opened', data.jitai_log_id);
    } catch (e: any) {
      log('[EMA] next failed:', e?.response?.status, e?.response?.data ?? e?.message);
      setPhase('error');
    }
  }, [logEngagement]);

  useEffect(() => {
    if (!visible) return;
    load();
  }, [visible]);

  useEffect(() => {
    if (phase !== 'form' || !survey) return;
    const remaining = msUntil(survey.expires_at);
    if (remaining === null || remaining > MAX_TIMEOUT_MS) return;
    if (remaining <= 0) {
      setPhase('expired');
      return;
    }
    const timer = setTimeout(() => setPhase('expired'), remaining);
    return () => clearTimeout(timer);
  }, [phase, survey]);

  function setAnswer(subItemId: string, value: EMAAnswerValue) {
    setAnswers((prev) => pruneHiddenAnswers(items, { ...prev, [subItemId]: value }));
  }

  function handleDismiss() {
    if (submitting) return;
    if (phase === 'form') logEngagement('ema_dismissed', survey?.jitai_log_id);
    onClose();
  }

  async function handleSubmit() {
    if (!survey || !complete || submitting) return;
    setSubmitting(true);

    try {
      await emaApi.submitResponses({
        prompt_id: survey.prompt_id,
        ema_type: survey.ema_type,
        jitai_log_id: survey.jitai_log_id ?? jitaiLogId ?? null,
        outcome_window_start: survey.outcome_window_start ?? null,
        outcome_window_end: survey.outcome_window_end ?? null,
        responses: visibleItems.map((sub) => ({
          sub_item_id: sub.sub_item_id,
          value: answers[sub.sub_item_id],
        })),
      });
      logEngagement('ema_completed', survey.jitai_log_id);
      onClose();
    } catch (e: any) {
      const status = e?.response?.status;
      log('[EMA] submit failed:', status, e?.response?.data);
      Alert.alert(
        'Could not submit',
        status === 400 || status === 404
          ? 'This check-in is no longer valid. Close and try again later.'
          : 'Check your connection and try again.',
      );
      setSubmitting(false);
    }
  }

  function renderSection(item: EMAItem) {
    const visible = (item.sub_items ?? []).filter((sub) => isSubItemVisible(sub, answers));
    if (visible.length === 0) return null;

    const blocks: React.ReactNode[] = [];
    let i = 0;
    while (i < visible.length) {
      const sub = visible[i];
      if (sub.group) {
        const groupId = sub.group;
        const groupText =
          sub.group_text ?? visible.find((entry) => entry.group === groupId && entry.group_text)?.group_text;
        const grouped: EMASubItem[] = [];
        while (i < visible.length && visible[i].group === groupId) {
          grouped.push(visible[i]);
          i += 1;
        }
        blocks.push(
          <View key={groupId} style={styles.group}>
            {groupText ? <Text style={styles.groupText}>{groupText}</Text> : null}
            {grouped.map((entry) => (
              <EMASubItemField
                key={entry.sub_item_id}
                sub={entry}
                value={answers[entry.sub_item_id]}
                onChange={(value) => setAnswer(entry.sub_item_id, value)}
              />
            ))}
          </View>,
        );
        continue;
      }

      blocks.push(
        <EMASubItemField
          key={sub.sub_item_id}
          sub={sub}
          value={answers[sub.sub_item_id]}
          onChange={(value) => setAnswer(sub.sub_item_id, value)}
        />,
      );
      i += 1;
    }

    return (
      <View key={item.item_id} style={styles.section}>
        <Text style={styles.sectionTitle}>{item.title}</Text>
        {blocks}
      </View>
    );
  }

  function renderBody() {
    if (phase === 'loading') {
      return (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#007AFF" />
        </View>
      );
    }

    if (phase === 'error') {
      return (
        <View style={styles.centered}>
          <Text style={styles.messageTitle}>Could not load your check-in</Text>
          <Text style={styles.messageBody}>Check your connection and try again.</Text>
          <TouchableOpacity style={styles.retry} onPress={load}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      );
    }

    if (phase === 'noshow') {
      return (
        <View style={styles.centered}>
          <Text style={styles.messageTitle}>Nothing to do right now</Text>
          <Text style={styles.messageBody}>
            {(noShowReason && NO_SHOW_COPY[noShowReason]) ?? NO_SHOW_FALLBACK}
          </Text>
        </View>
      );
    }

    if (phase === 'expired') {
      return (
        <View style={styles.centered}>
          <Text style={styles.messageTitle}>This check-in has closed</Text>
          <Text style={styles.messageBody}>
            The response window has passed. You will be prompted again later.
          </Text>
        </View>
      );
    }

    return (
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        {typeof survey?.daily_cap === 'number' && (
          <Text style={styles.progress}>
            Check-in {survey.daily_count + 1} of {survey.daily_cap}
          </Text>
        )}

        {survey?.outcome_window_active && (
          <Text style={styles.windowNote}>Follow-up check-in</Text>
        )}

        {items.map(renderSection)}
      </ScrollView>
    );
  }

  return (
    <Modal visible={visible} animationType="slide" transparent={false} onRequestClose={handleDismiss}>
      <View style={styles.container}>
        <View style={styles.header}>
          <TouchableOpacity onPress={handleDismiss} style={styles.close} accessibilityLabel="Dismiss survey">
            <Text style={styles.closeText}>✕</Text>
          </TouchableOpacity>
        </View>

        {renderBody()}

        <View style={styles.footer}>
          {phase === 'form' ? (
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
          ) : phase === 'noshow' || phase === 'expired' ? (
            <TouchableOpacity style={styles.submit} onPress={onClose}>
              <Text style={styles.submitText}>Close</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      </View>
    </Modal>
  );
}

function msUntil(iso: string | null | undefined) {
  if (!iso) return null;
  const at = new Date(iso).getTime();
  return Number.isFinite(at) ? at - Date.now() : null;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff', paddingTop: 60 },
  header: { flexDirection: 'row', justifyContent: 'flex-end', paddingHorizontal: 16 },
  close: { padding: 8 },
  closeText: { fontSize: 20, color: '#888' },
  body: { paddingHorizontal: 24, paddingTop: 12, paddingBottom: 24 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 },
  progress: { fontSize: 13, color: '#888', fontWeight: '600', marginBottom: 8 },
  windowNote: { fontSize: 13, color: '#007AFF', fontWeight: '600', marginBottom: 20 },
  section: { marginBottom: 12 },
  sectionTitle: { fontSize: 20, fontWeight: '700', color: '#111', marginBottom: 16 },
  group: { marginBottom: 8 },
  groupText: { fontSize: 16, fontWeight: '600', color: '#111', marginBottom: 12 },
  messageTitle: { fontSize: 18, fontWeight: '700', color: '#111', marginBottom: 8, textAlign: 'center' },
  messageBody: { fontSize: 15, color: '#666', textAlign: 'center', lineHeight: 21 },
  retry: { marginTop: 20, paddingVertical: 10, paddingHorizontal: 24 },
  retryText: { fontSize: 16, color: '#007AFF', fontWeight: '600' },
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
