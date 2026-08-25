export type PushType = 'ema_prompt' | 'checkin_reminder';

export type ParsedPush = {
  type: PushType | null;
  promptId?: string;
  jitaiLogId?: number;
};

export function parsePushData(data: Record<string, unknown> | undefined | null): ParsedPush {
  if (!data) return { type: null };

  const rawType = data.type;
  const type: PushType | null =
    rawType === 'ema_prompt' || rawType === 'checkin_reminder' ? rawType : null;

  const rawId = data.jitai_log_id;
  let jitaiLogId: number | undefined;
  if (typeof rawId === 'number' && Number.isFinite(rawId)) {
    jitaiLogId = rawId;
  } else if (typeof rawId === 'string' && rawId !== '') {
    const parsed = Number(rawId);
    if (Number.isFinite(parsed)) jitaiLogId = parsed;
  }

  const promptId = typeof data.prompt_id === 'string' && data.prompt_id ? data.prompt_id : undefined;

  return { type, promptId, jitaiLogId };
}

export function shouldOpenEMA(type: PushType | null): boolean {
  return type === 'ema_prompt' || type === 'checkin_reminder';
}
