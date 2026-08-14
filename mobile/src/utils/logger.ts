const REDACTED = '[REDACTED]';

const SENSITIVE_KEYS = [
  'password',
  'access',
  'refresh',
  'token',
  'push_token',
  'authorization',
  'auth_tokens',
];

function isSensitiveKey(key: string): boolean {
  const lower = key.toLowerCase();
  return SENSITIVE_KEYS.some((k) => lower === k || lower.includes(k));
}

export function redact(value: unknown, depth = 0): unknown {
  if (depth > 6 || value === null || value === undefined) return value;

  if (Array.isArray(value)) {
    return value.map((v) => redact(v, depth + 1));
  }

  if (typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = isSensitiveKey(k) ? REDACTED : redact(v, depth + 1);
    }
    return out;
  }

  return value;
}

export function log(...args: unknown[]): void {
  if (!__DEV__) return;
  console.log(...args.map((a) => (typeof a === 'object' ? redact(a) : a)));
}
