import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo from '@react-native-community/netinfo';
import { telemetry } from '../api/endpoints';
import { TelemetryEvent } from './telemetryStore';
import { log } from '../utils/logger';

const QUEUE_KEY = 'telemetry_offline_queue';
let isFlushing = false;

export async function enqueue(event: TelemetryEvent): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_KEY);
    const queue: TelemetryEvent[] = raw ? JSON.parse(raw) : [];
    queue.push(event);
    await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
    log('[OfflineQueue] Queued event:', event.event_type);
  } catch (e) {
    log('[OfflineQueue] Failed to enqueue:', e);
  }
}

export async function flushQueue(): Promise<void> {
  if (isFlushing) return;
  isFlushing = true;

  try {
    const raw = await AsyncStorage.getItem(QUEUE_KEY);
    const queue: TelemetryEvent[] = raw ? JSON.parse(raw) : [];
    if (queue.length === 0) return;

    log('[OfflineQueue] Flushing', queue.length, 'events');
    const failed: TelemetryEvent[] = [];

    for (const event of queue) {
      try {
        await telemetry.logPhone({
          session_id: event.session_id,
          event_type: event.event_type,
          occurred_at: event.occurred_at,
          screen_name: event.screen_name,
          metadata: event.metadata,
        });
      } catch (e) {
        log('[OfflineQueue] Event failed, keeping:', (e as any)?.message);
        failed.push(event);
      }
    }

    await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(failed));
    log('[OfflineQueue] Done —', failed.length, 'still pending');
  } catch (e) {
    log('[OfflineQueue] Flush error:', e);
  } finally {
    isFlushing = false;
  }
}

export function startNetworkListener(): () => void {
  const unsubscribe = NetInfo.addEventListener((state) => {
    const online = !!state.isConnected;
    log('[Network]', online ? 'ONLINE' : 'OFFLINE', state.type);

    if (online) {
      setTimeout(flushQueue, 5000);
    }
  });
  return unsubscribe;
}
