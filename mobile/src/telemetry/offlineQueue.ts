import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo from '@react-native-community/netinfo';
import { telemetry } from '../api/endpoints';
import { TelemetryEvent } from './telemetryStore';

const QUEUE_KEY = 'telemetry_offline_queue';

export async function enqueue(event: TelemetryEvent): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_KEY);
    const queue: TelemetryEvent[] = raw ? JSON.parse(raw) : [];
    queue.push(event);
    await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
    console.log('[OfflineQueue] Queued event:', event.event_type);
  } catch (e) {
    console.log('[OfflineQueue] Failed to enqueue:', e);
  }
}

export async function flushQueue(): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_KEY);
    if (!raw) return;
    const queue: TelemetryEvent[] = JSON.parse(raw);
    if (queue.length === 0) return;

    console.log('[OfflineQueue] Flushing', queue.length, 'events');
    const failed: TelemetryEvent[] = [];

    for (const event of queue) {
      try {
        await telemetry.logPhone(event);
      } catch {
        failed.push(event);
      }
    }

    await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(failed));
    console.log('[OfflineQueue] Done —', failed.length, 'still pending');
  } catch (e) {
    console.log('[OfflineQueue] Flush error:', e);
  }
}

export function startNetworkListener(): () => void {
  const unsubscribe = NetInfo.addEventListener((state) => {
    if (state.isConnected) {
      flushQueue();
    }
  });
  return unsubscribe;
}
