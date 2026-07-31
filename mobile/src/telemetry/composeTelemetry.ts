import { useTelemetryStore, TelemetryEvent } from './telemetryStore';
import { useAuthStore } from '../store/authStore';

function generateSessionId(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

let currentSessionId = generateSessionId();

type ComposeSession = {
  startTime: number | null;
  keystrokeCount: number;
  deleteCount: number;
  started: boolean;
};

let session: ComposeSession = {
  startTime: null,
  keystrokeCount: 0,
  deleteCount: 0,
  started: false,
};

export function resetComposeSession() {
  session = {
    startTime: null,
    keystrokeCount: 0,
    deleteCount: 0,
    started: false,
  };
  currentSessionId = generateSessionId();
}

function buildPayload(event_type: TelemetryEvent['event_type']): TelemetryEvent {
  const now = new Date();
  const userId = useAuthStore.getState().userId;
  return {
    user: userId,
    session_id: currentSessionId,
    event_type,
    occurred_at: now.toISOString(),
    screen_name: 'compose',
    metadata: {
      keystroke_count: session.keystrokeCount,
      delete_count: session.deleteCount,
      time_on_compose: session.startTime ? now.getTime() - session.startTime : 0,
    },
  };
}

export function onComposeChange(prevText: string, nextText: string) {
  const { push } = useTelemetryStore.getState();

  // First keystroke — start session and emit draft_started
  if (!session.started && nextText.length > 0) {
    session.started = true;
    session.startTime = Date.now();
    session.keystrokeCount = 1;
    session.deleteCount = 0;
    push(buildPayload('draft_started'));
    return;
  }

  const diff = nextText.length - prevText.length;
  if (diff < 0) {
    session.deleteCount += Math.abs(diff);
  } else if (diff > 0) {
    session.keystrokeCount += diff;
  }
}

export function emitDraftDeleted() {
  const { push } = useTelemetryStore.getState();
  if (session.started) {
    push(buildPayload('draft_deleted'));
  }
  resetComposeSession();
}

export function emitDraftSubmitted() {
  const { push } = useTelemetryStore.getState();
  if (session.started) {
    push(buildPayload('draft_submitted'));
  }
  resetComposeSession();
}
