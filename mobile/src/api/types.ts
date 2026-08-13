export type EMAType = 'scheduled_check_in' | 'post_prompt' | 'extra_check_in';

export type EMAItem = {
  item_id: string;
  label: string;
  response_type: string;
  min_value: number;
  max_value: number;
};

export type EMANextShowResponse = {
  should_show: true;
  prompt_id: string;
  ema_type: EMAType;
  jitai_log_id: number | null;
  outcome_window_active: boolean;
  outcome_window_start?: string | null;
  outcome_window_end?: string | null;
  expires_at: string;
  daily_cap: number;
  daily_count: number;
  items: EMAItem[];
};

export type EMANextNoShowResponse = {
  should_show: false;
  reason: string;
  outcome_window_active: boolean;
  outcome_window_start?: string | null;
  outcome_window_end?: string | null;
  daily_cap?: number;
  daily_count?: number;
};

export type EMANextResponse = EMANextShowResponse | EMANextNoShowResponse;

export type EMAAnswer = {
  item_id: string;
  value: number;
};

export type EMASubmitRequest = {
  prompt_id: string;
  ema_type?: EMAType;
  jitai_log_id?: number | null;
  outcome_window_start?: string | null;
  outcome_window_end?: string | null;
  responses: EMAAnswer[];
};

export type EMAStoredAnswer = {
  id: number;
  item_id: string;
  value: number;
};

export type EMASubmitResponse = {
  id: number;
  user: number;
  prompt_id: string;
  sent_at: string;
  responded_at: string;
  status: 'completed';
  ema_type: EMAType;
  source_jitai_log?: number | null;
  outcome_window_start?: string | null;
  outcome_window_end?: string | null;
  expires_at?: string | null;
  mood?: number | null;
  stress?: number | null;
  energy?: number | null;
  item_responses: EMAStoredAnswer[];
};
