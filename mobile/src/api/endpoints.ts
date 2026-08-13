import client from './client';
import { EMANextResponse, EMASubmitRequest, EMASubmitResponse } from './types';

// ─── Auth ────────────────────────────────────────────────────────────────────

export const auth = {
  checkEmail: (email: string) =>
    client.post('/user/checkemail/', { email }),

  register: (body: {
    email: string;
    password: string;
    first_name?: string;
    last_name?: string;
    birthdate?: string;
    gender?: 'male' | 'female' | 'other';
    height_feet?: string;
    height_inches?: string;
    goal_weight?: number;
    goal_to_lose_weight?: boolean;
    goal_to_feel_better?: boolean;
  }) => client.post('/user/', body),

  login: (email: string, password: string) =>
    client.post('/user/login/', { email, password }),

  refresh: (refreshToken: string) =>
    client.post('/auth/token/refresh/', { refresh: refreshToken }),

  me: () =>
    client.get('/auth/me/'),
};

// ─── User ─────────────────────────────────────────────────────────────────────

export const user = {
  update: (userId: number, body: {
    push_token?: string;
    first_name?: string;
    last_name?: string;
    height_feet?: string;
    height_inches?: string;
    goal_weight?: number;
  }) => client.put(`/user/${userId}/`, body),
};

// ─── EMA ──────────────────────────────────────────────────────────────────────

export const ema = {
  next: () =>
    client.get<EMANextResponse>('/ema/next/'),

  submitResponses: (body: EMASubmitRequest) =>
    client.post<EMASubmitResponse>('/ema/responses/', body),
};


// ─── JITAI ───────────────────────────────────────────────────────────────────

export const jitai = {
  receipt: (body: {
    jitai_log_id: number;
    device_received_at: string;
    platform?: string;
    app_state?: string;
  }) => client.post('/jitai/receipt/', body),
};

// ─── Telemetry ────────────────────────────────────────────────────────────────

export const telemetry = {
  logPhone: (body: {
    user: number | null;
    session_id: string;
    event_type: 'session_start' | 'session_end' | 'draft_started' | 'draft_deleted' | 'draft_submitted';
    occurred_at: string;
    screen_name?: string;
    metadata?: Record<string, unknown>;
  }) => client.post('/telemetry/phone/', body),

  logEngagement: (body: {
    user: number;
    event_type: 'notification_tapped' | 'notification_dismissed' | 'ema_opened' | 'ema_dismissed' | 'ema_completed';
    occurred_at: string;
    jitai_log?: number;
  }) => client.post('/telemetry/engagement/', body),
};
