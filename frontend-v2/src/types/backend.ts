export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export type AsrProfile = 'fast' | 'balanced' | 'accurate';

export interface LlmOptions {
  base_url: string;
  api_key: string;
  model: string;
  llm_support_json: boolean;
}

export interface WhisperOptions {
  runtime: 'cloud' | 'local';
  model: string;
  language: string;
  base_url: string;
  api_key: string;
}

export interface SubtitleJobOptions {
  enable_demucs: boolean;
  asr_profile: AsrProfile;
  asr_fallback_enabled: boolean;
  asr_allow_cloud_fallback: boolean;
  asr_allow_local_fallback: boolean;
  enable_diarization: boolean;
  source_language: string;
  target_language: string;
  llm: LlmOptions;
  whisper: WhisperOptions;
}

export type SubtitleJobOptionsPayload = Partial<Omit<SubtitleJobOptions, 'llm' | 'whisper' | 'asr_profile'>> & Pick<SubtitleJobOptions, 'llm' | 'whisper' | 'asr_profile'>;

export interface JobCreateResponse {
  job_id: string;
  status: JobStatus;
  source_url_policy?: SourceUrlPolicyResult | null;
}

export interface SourceUrlPolicyResult {
  normalized_url: string;
  host: string;
  allowed: boolean;
  reason: string;
}

export interface JobStageDetail {
  stage: string;
  step_key: string;
  step_label: string;
  done: number;
  total: number;
  unit: string;
  percent_in_stage: number;
  eta_seconds?: number | null;
  updated_at?: string | null;
}

export interface JobProgressEvent {
  event_id: string;
  stage: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  percent: number;
  at: string;
}

export interface SubtitleSyncDiagnostics {
  alignment_quality_score: number;
  global_offset_ms: number;
  drift_scale: number;
  correction_applied: boolean;
  correction_method: string;
  triggered?: boolean;
  correction_score?: number;
}

export interface JobStatusResponse {
  job_id: string;
  status: JobStatus;
  progress_percent: number;
  current_stage: string;
  message: string;
  error: string | null;
  error_code?: string;
  partial_result?: SubtitleJobResult | null;
  started_at: string | null;
  updated_at: string | null;
  cancel_requested: boolean;
  whisper_runtime: string;
  whisper_model_requested: string;
  whisper_model_effective: string;
  asr_provider_effective: string;
  asr_fallback_used: boolean;
  queue_ahead: number;
  worker_alive: boolean;
  stage_durations_ms: Record<string, number>;
  stage_order: string[];
  total_duration_ms: number;
  stage_detail?: JobStageDetail | null;
  recent_progress_events?: JobProgressEvent[];
  status_revision?: number;
  poll_interval_ms_hint?: number;
  sync_diagnostics?: SubtitleSyncDiagnostics | null;
}

export interface LegacySubtitle {
  id: number;
  start: number;
  end: number;
  text: string;
  translation: string;
  index: number;
}

export interface WordSegment {
  id: number;
  start: number;
  end: number;
  word: string;
  confidence?: number | null;
  asr_segment_index: number;
  source: 'cloud' | 'local';
}

export interface SubtitleJobResult {
  subtitles: LegacySubtitle[];
  bilingual_srt: string;
  source_srt: string;
  stats: Record<string, unknown>;
  word_segments: WordSegment[];
  diagnostics?: SubtitleSyncDiagnostics | null;
  partial?: boolean;
  partial_stage?: string;
  partial_error?: string;
}

export interface SubtitleConfigProbeResult {
  ok: boolean;
  message: string;
  detail?: string | null;
}

export interface SubtitleConfigTestResponse {
  status: 'ok' | 'partial' | 'failed';
  llm: SubtitleConfigProbeResult;
  whisper: SubtitleConfigProbeResult;
}

export interface WhisperLocalModelStatus {
  model: string;
  installed: boolean;
  cache_path: string;
}

export interface WhisperLocalModelsResponse {
  status: 'ok' | 'degraded';
  dependency_ok: boolean;
  message: string;
  models: WhisperLocalModelStatus[];
}

export interface HealthResponse {
  status: 'ok';
  service: string;
  version: string;
  capabilities: Record<string, boolean>;
}

export interface SubtitleTaskMeta {
  pending_state: 'none' | 'failed' | 'cancelled';
  last_job_id: string;
  last_job_status: JobStatus;
  last_stage: string;
  last_message: string;
  has_partial_result: boolean;
  source_mode: 'file' | 'url' | 'resume';
  updated_at: number;
}

export interface HistoryRecord {
  videoName: string;
  srtName: string;
  currentIndex: number;
  totalSentences: number;
  thumbnail: string;
  timestamp: number;
  completed: boolean;
  historyId: string;
  displayName: string;
  folderId: string;
  subtitleTaskMeta?: SubtitleTaskMeta | null;
}

export interface HistoryRecordsResponse {
  records: HistoryRecord[];
}

export interface HistoryRecordsSyncResponse {
  status: 'ok';
  saved_count: number;
  records: HistoryRecord[];
}

export interface SubtitleJobFromUrlRequest {
  url: string;
  options: SubtitleJobOptionsPayload;
}

export interface ReadingSourceSummary {
  video_name: string;
  srt_name: string;
  subtitle_count: number;
  updated_at: number;
  has_summary_terms: boolean;
}

export interface ReadingSourcesResponse {
  sources: ReadingSourceSummary[];
}

export type EnglishLevel = 'junior' | 'senior' | 'cet4' | 'cet6' | 'kaoyan' | 'toefl' | 'sat';
export type LlmMode = 'unified' | 'custom';
export type ReadingScope = 'all' | 'intensive' | 'extensive';
export type ReadingRatioPreset = 'high_energy' | 'long_term' | 'low_energy';
export type ReadingDifficultyTier = 'very_easy' | 'easy' | 'balanced' | 'challenging' | 'hard';
export type ReadingGenre = 'news' | 'science' | 'story' | 'workplace';
export type ReadingMaterialKind = 'intensive' | 'extensive';

export interface ProfileSettings {
  english_level: EnglishLevel;
  english_level_numeric: number;
  english_level_cefr: string;
  llm_mode: LlmMode;
  llm_unified: LlmOptionsPublic;
  llm_listening: LlmOptionsPublic;
  llm_reading: LlmOptionsPublic;
  updated_at: number;
}

export interface LlmOptionsPublic {
  base_url: string;
  model: string;
  llm_support_json: boolean;
  api_key_masked: string;
  has_api_key: boolean;
}

export interface LlmOptionsUpdate {
  base_url: string;
  model: string;
  llm_support_json: boolean;
}

export interface ProfileSettingsUpdateRequest {
  english_level?: EnglishLevel;
  llm_mode?: LlmMode;
  llm_unified?: LlmOptionsUpdate;
  llm_listening?: LlmOptionsUpdate;
  llm_reading?: LlmOptionsUpdate;
}

export interface ProfileKeysUpdateRequest {
  llm_unified_api_key?: string;
  llm_listening_api_key?: string;
  llm_reading_api_key?: string;
}

export interface ProfileKeysUpdateResponse {
  status: 'ok';
  updated_fields: string[];
}

export interface AuthRegisterRequest {
  username: string;
  password: string;
}

export interface AuthLoginRequest {
  username: string;
  password: string;
}

export interface AuthUserResponse {
  user_id: string;
  username: string;
  created_at: number;
}

export interface AuthTokenResponse {
  token_type: 'bearer';
  access_token: string;
  expires_at: number;
  user: AuthUserResponse;
}

export interface AuthLogoutResponse {
  status: 'ok';
}

export interface WalletQuotaResponse {
  user_id: string;
  username: string;
  quota: number;
  used_quota: number;
  remaining_quota: number;
  request_count: number;
}

export interface WalletRedeemRequest {
  key: string;
}

export interface WalletRedeemResponse extends WalletQuotaResponse {
  status: 'ok';
  added_quota: number;
}

export interface WalletPackItem {
  id: string;
  label: string;
  price: number;
  quota: number;
  description: string;
}

export interface WalletPacksResponse {
  packs: WalletPackItem[];
  cost_multiplier: number;
}

export interface ReadingChoiceQuestion {
  question_id: string;
  question: string;
  choices: string[];
  answer_index: number;
  explanation: string;
}

export interface ReadingShortQuestion {
  question_id: string;
  question: string;
  reference_answer: string;
}

export interface ReadingQuizPayload {
  choice_questions: ReadingChoiceQuestion[];
  short_questions: ReadingShortQuestion[];
}

export interface ReadingMaterialSlot {
  kind: ReadingMaterialKind;
  text: string;
  word_count: number;
  target_word_count: number;
  generated: boolean;
}

export interface ReadingDifficultyReport {
  source_score: number;
  source_level: number;
  generated_level: number;
  target_level: number;
  gap_to_user: number;
  recommended_ratio_preset: ReadingRatioPreset;
  hit_i_plus_one: boolean;
  used_cefr_fallback: boolean;
  detail: Record<string, unknown>;
}

export interface ReadingConfigSnapshot {
  scope: ReadingScope;
  ratio_preset: ReadingRatioPreset;
  difficulty_tier: ReadingDifficultyTier;
  genre: ReadingGenre;
  word_budget_total: number;
}

export interface ReadingVersion {
  version_id: string;
  video_name: string;
  srt_name: string;
  user_level: EnglishLevel | string;
  scope: ReadingScope;
  ratio_preset: ReadingRatioPreset;
  difficulty_tier: ReadingDifficultyTier;
  genre: ReadingGenre;
  i_plus_one_hit: boolean;
  pipeline_version: string;
  config: ReadingConfigSnapshot;
  difficulty_report: ReadingDifficultyReport;
  materials: ReadingMaterialSlot[];
  quiz: ReadingQuizPayload;
  created_at: number;
  updated_at: number;
}

export interface ReadingHistoryItem {
  version_id: string;
  video_name: string;
  srt_name: string;
  scope: ReadingScope;
  ratio_preset: ReadingRatioPreset;
  difficulty_tier: ReadingDifficultyTier;
  genre: ReadingGenre;
  i_plus_one_hit: boolean;
  created_at: number;
  updated_at: number;
  has_intensive: boolean;
  has_extensive: boolean;
}

export interface ReadingHistoryResponse {
  items: ReadingHistoryItem[];
  offset: number;
  limit: number;
  has_more: boolean;
}

export interface ReadingVersionResponse {
  version: ReadingVersion;
}

export interface ReadingMaterialResponse extends ReadingVersion {
  cached: boolean;
}

export interface ReadingScoreDimension {
  name: string;
  score: number;
  max_score: number;
  comment: string;
}

export interface ReadingShortAnswerSubmitRequest {
  version_id: string;
  question_id: string;
  answer_text: string;
}

export interface ReadingShortAnswerSubmitResponse {
  attempt_id: string;
  version_id: string;
  question_id: string;
  answer_text: string;
  total_score: number;
  max_score: number;
  dimensions: ReadingScoreDimension[];
  overall_comment: string;
  reference_answer: string;
  submitted_at: number;
}

export interface ReadingShortAnswerHistoryItem {
  attempt_id: string;
  version_id: string;
  question_id: string;
  answer_text: string;
  total_score: number;
  max_score: number;
  dimensions: ReadingScoreDimension[];
  overall_comment: string;
  reference_answer: string;
  submitted_at: number;
}

export interface ReadingShortAnswerHistoryResponse {
  items: ReadingShortAnswerHistoryItem[];
}

export interface ReadingShortAnswerHistoryDeleteRequest {
  version_id: string;
  question_id: string;
}

export interface DeleteOperationResponse {
  status: 'ok';
  deleted_count: number;
}

export interface ReadingMaterialGenerateRequest {
  video_name: string;
  srt_name: string;
  user_level: EnglishLevel | string;
  scope?: ReadingScope;
  ratio_preset?: ReadingRatioPreset;
  difficulty_tier?: ReadingDifficultyTier;
  genre?: ReadingGenre;
  force_regenerate?: boolean;
  llm?: LlmOptions | null;
}
