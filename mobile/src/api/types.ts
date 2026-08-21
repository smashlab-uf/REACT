export type EMAType = 'scheduled_check_in' | 'post_prompt' | 'extra_check_in';

export type EMAResponseType = 'likert' | 'single_choice' | 'multi_choice' | 'yes_no' | 'number';

export type EMAAnswerValue = number | string | string[];

export type EMADependsOn = {
  sub_item_id: string;
  equals?: string;
  not_equals?: string;
};

export type EMASubItem = {
  sub_item_id: string;
  text: string;
  response_type: EMAResponseType | string;
  min_value?: number;
  max_value?: number;
  low_label?: string;
  high_label?: string;
  choices?: string[];
  group?: string;
  group_text?: string;
  depends_on?: EMADependsOn;
  schedule_condition?: string;
};

export type EMAItem = {
  item_id: string;
  title: string;
  sub_items: EMASubItem[];
  label?: string;
  response_type?: string;
  min_value?: number;
  max_value?: number;
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
  item_id?: string;
  sub_item_id?: string;
  value: EMAAnswerValue;
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
  sub_item_id?: string;
  response_type?: string;
  value: EMAAnswerValue;
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
