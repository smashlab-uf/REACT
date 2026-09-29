import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  LayoutAnimation,
  Linking,
  Modal,
  NativeScrollEvent,
  NativeSyntheticEvent,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  UIManager,
  View,
} from 'react-native';

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

const HEADER_TITLE_SWAP = LayoutAnimation.create(180, LayoutAnimation.Types.easeInEaseOut, LayoutAnimation.Properties.opacity);
const STICK_THRESHOLD = 12;
import EMASubItemField from '../components/EMASubItemField';
import { ema as emaApi, telemetry } from '../api/endpoints';
import {
  EMAAnswerValue,
  EMAItem,
  EMANextShowResponse,
  EMASubItem,
  ResourceCard,
} from '../api/types';
import { isAnswered, isSubItemVisible, pruneHiddenAnswers, visibleSubItems } from '../ema/visibility';
import { useAuthStore } from '../store/authStore';
import { useAlertStore } from '../store/alertStore';
import { log } from '../utils/logger';
import { colors, radius, typography } from '../theme';

const NO_SHOW_COPY: Record<string, string> = {
  daily_cap_reached: "You're all caught up on check-ins for today. Thanks for taking part!",
  outcome_window_already_completed: "You've already completed this check-in. Thank you!",
};

const NO_SHOW_FALLBACK = "You're all caught up! There's no check-in for you right now.";

const MAX_TIMEOUT_MS = 2147483647;

type Phase = 'loading' | 'form' | 'noshow' | 'expired' | 'error' | 'resources';

const DIALABLE = /^[0-9+\-\s()]+$/;

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
  const showAlert = useAlertStore((s) => s.show);

  const [phase, setPhase] = useState<Phase>('loading');
  const [survey, setSurvey] = useState<EMANextShowResponse | null>(null);
  const [noShowReason, setNoShowReason] = useState<string | null>(null);
  const [resourceCard, setResourceCard] = useState<ResourceCard | null>(null);
  const [answers, setAnswers] = useState<Record<string, EMAAnswerValue>>({});
  const [submitting, setSubmitting] = useState(false);
  const [activeSectionTitle, setActiveSectionTitle] = useState<string | null>(null);
  const sectionOffsets = useRef<Record<string, number>>({});

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
    setResourceCard(null);
    setAnswers({});
    setSubmitting(false);
    setActiveSectionTitle(null);
    sectionOffsets.current = {};

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

  function handleScroll(e: NativeSyntheticEvent<NativeScrollEvent>) {
    const scrollY = e.nativeEvent.contentOffset.y;
    let next: string | null = null;
    for (const item of items) {
      const y = sectionOffsets.current[item.item_id];
      if (y === undefined) continue;
      if (scrollY + STICK_THRESHOLD >= y) {
        next = item.title;
      } else {
        break;
      }
    }
    if (next !== activeSectionTitle) {
      LayoutAnimation.configureNext(HEADER_TITLE_SWAP);
      setActiveSectionTitle(next);
    }
  }

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
      const { data: submitted } = await emaApi.submitResponses({
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
      if (submitted.resource_card) {
        setResourceCard(submitted.resource_card);
        setSubmitting(false);
        setPhase('resources');
        return;
      }
      onClose();
    } catch (e: any) {
      const status = e?.response?.status;
      log('[EMA] submit failed:', status, e?.response?.data);
      showAlert(
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
      <View
        key={item.item_id}
        style={styles.section}
        onLayout={(e) => {
          sectionOffsets.current[item.item_id] = e.nativeEvent.layout.y;
        }}>
        <Text style={styles.sectionTitle}>{item.title}</Text>
        {blocks}
      </View>
    );
  }

  function renderBody() {
    if (phase === 'loading') {
      return (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={colors.primary} />
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
          <Text style={styles.messageTitle}>You're all caught up!</Text>
          <Text style={styles.messageBody}>
            {(noShowReason && NO_SHOW_COPY[noShowReason]) ?? NO_SHOW_FALLBACK}
          </Text>
        </View>
      );
    }

    if (phase === 'resources' && resourceCard) {
      return (
        <ScrollView contentContainerStyle={styles.body}>
          <Text style={styles.sectionTitle}>{resourceCard.title}</Text>
          <Text style={styles.resourceMessage}>{resourceCard.message}</Text>
          {resourceCard.resources.map((resource) => (
            <View key={resource.name} style={styles.resource}>
              <Text style={styles.resourceName}>{resource.name}</Text>
              {resource.description ? (
                <Text style={styles.messageBody}>{resource.description}</Text>
              ) : null}
              {resource.contact && DIALABLE.test(resource.contact) ? (
                <TouchableOpacity
                  style={styles.resourceCall}
                  onPress={() => Linking.openURL(`tel:${resource.contact!.replace(/\s/g, '')}`)}
                  accessibilityLabel={`Call ${resource.name}`}>
                  <Text style={styles.resourceCallText}>Call {resource.contact}</Text>
                </TouchableOpacity>
              ) : resource.contact ? (
                <Text style={styles.resourceContact}>{resource.contact}</Text>
              ) : null}
            </View>
          ))}
        </ScrollView>
      );
    }

    if (phase === 'expired') {
      return (
        <View style={styles.centered}>
          <Text style={styles.messageTitle}>This check-in has closed</Text>
          <Text style={styles.messageBody}>
            The response window has passed. We'll check in with you again soon.
          </Text>
        </View>
      );
    }

    return (
      <ScrollView
        contentContainerStyle={styles.body}
        keyboardShouldPersistTaps="handled"
        onScroll={handleScroll}
        scrollEventThrottle={16}>
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
          <View style={styles.headerSide} />

          <View style={styles.headerCenter}>
            <Text
              key={activeSectionTitle ?? 'default'}
              style={activeSectionTitle ? styles.headerActiveTitle : styles.headerTitle}
              numberOfLines={1}>
              {activeSectionTitle ?? 'Check-in'}
            </Text>
            {!activeSectionTitle && phase === 'form' && typeof survey?.daily_cap === 'number' && (
              <Text style={styles.progressPillText}>
                {survey.daily_count + 1} of {survey.daily_cap}
              </Text>
            )}
          </View>

          <View style={[styles.headerSide, styles.headerSideRight]}>
            <TouchableOpacity onPress={handleDismiss} style={styles.close} accessibilityLabel="Dismiss survey">
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          </View>
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
          ) : phase === 'noshow' || phase === 'expired' || phase === 'resources' ? (
            <TouchableOpacity style={styles.submit} onPress={onClose}>
              <Text style={styles.submitText}>{phase === 'resources' ? 'Done' : 'Close'}</Text>
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
  container: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.primary,
    paddingTop: 60,
    paddingHorizontal: 16,
    paddingBottom: 16,
    borderBottomWidth: 3,
    borderBottomColor: colors.accent,
  },
  headerSide: { flex: 1 },
  headerSideRight: { flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: 10 },
  headerCenter: { flex: 2, alignItems: 'center' },
  headerTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.75)',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  headerActiveTitle: { fontSize: 17, fontWeight: '700', color: colors.onPrimary },
  progressPill: {
    backgroundColor: 'rgba(255,255,255,0.18)',
    borderRadius: radius.pill,
    paddingHorizontal: 12,
    paddingVertical: 4,
    alignItems: 'center',
    justifyContent: 'center',
  },
  progressPillText: { fontSize: 13, fontWeight: '700', color: colors.onPrimary },
  close: { padding: 8 },
  closeText: { fontSize: 20, color: colors.onPrimary },
  body: { paddingHorizontal: 24, paddingTop: 20, paddingBottom: 24 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 },
  windowNote: { fontSize: 13, color: colors.primary, fontWeight: '600', marginBottom: 20 },
  section: { marginBottom: 12 },
  sectionTitle: { ...typography.heading, marginBottom: 20 },
  group: { marginBottom: 8 },
  groupText: { ...typography.label, marginBottom: 12 },
  messageTitle: { fontSize: 20, fontWeight: '700', color: colors.textPrimary, marginBottom: 8, textAlign: 'center' },
  messageBody: { ...typography.body, textAlign: 'center', lineHeight: 21 },
  resourceMessage: { ...typography.body, lineHeight: 21, marginBottom: 20 },
  resource: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.md,
    padding: 16,
    marginBottom: 12,
  },
  resourceName: { ...typography.label, marginBottom: 4 },
  resourceContact: { ...typography.body, marginTop: 8, fontWeight: '600' },
  resourceCall: {
    backgroundColor: colors.primary,
    borderRadius: radius.md,
    padding: 12,
    alignItems: 'center',
    marginTop: 12,
  },
  resourceCallText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  retry: { marginTop: 20, paddingVertical: 10, paddingHorizontal: 24 },
  retryText: { fontSize: 16, color: colors.primary, fontWeight: '600' },
  footer: { paddingHorizontal: 24, paddingBottom: 40, paddingTop: 8 },
  submit: {
    backgroundColor: colors.primary,
    borderRadius: radius.md,
    padding: 16,
    alignItems: 'center',
  },
  submitDisabled: { backgroundColor: colors.disabled },
  submitText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
