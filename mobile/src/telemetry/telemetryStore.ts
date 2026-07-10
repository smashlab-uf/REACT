import { create } from 'zustand';
import { telemetry } from '../api/endpoints';

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
    console.log('[Telemetry]', JSON.stringify(event, null, 2));
    set((state) => ({ events: [...state.events, event] }));

    // Fire and forget — send immediately to API
    telemetry.logPhone(event).catch((e) => {
      console.log('[Telemetry] API error:', e?.response?.status, e?.message);
    });
  },

  flush: () => {
    const { events } = get();
    console.log('[Telemetry] Flushing', events.length, 'events');
    set({ events: [] });
  },
}));
