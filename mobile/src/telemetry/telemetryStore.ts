import { create } from 'zustand';
import { telemetry } from '../api/endpoints';
import { enqueue } from './offlineQueue';
import { log } from '../utils/logger';

export type TelemetryEvent = {
  user: number | null;
  session_id: string;
  event_type: 'draft_started' | 'draft_deleted' | 'draft_submitted';
  occurred_at: string;
  screen_name: string;
  metadata: Record<string, unknown>;
};

type TelemetryStore = {
  events: TelemetryEvent[];
  push: (event: TelemetryEvent) => void;
  flush: () => void;
};

export const useTelemetryStore = create<TelemetryStore>((set, get) => ({
  events: [],

  push: (event) => {
    log('[Telemetry]', JSON.stringify(event, null, 2));
    set((state) => ({ events: [...state.events, event] }));

    telemetry.logPhone({
      session_id: event.session_id,
      event_type: event.event_type,
      occurred_at: event.occurred_at,
      screen_name: event.screen_name,
      metadata: event.metadata,
    }).catch((e) => {
      log('[Telemetry] API error, queuing offline:', e?.response?.status, e?.message);
      enqueue(event);
    });
  },

  flush: () => {
    const { events } = get();
    log('[Telemetry] Flushing', events.length, 'events');
    set({ events: [] });
  },
}));
