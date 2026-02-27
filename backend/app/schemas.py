from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.provider_url_rules import DEFAULT_LLM_BASE_URL, DEFAULT_WHISPER_BASE_URL


JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class LlmOptions(BaseModel):
    base_url: str = Field(default=DEFAULT_LLM_BASE_URL)
    api_key: str = Field(default="")
    model: str = Field(default="tencent/Hunyuan-MT-7B")
    llm_support_json: bool = Field(default=False)


class WhisperOptions(BaseModel):
    runtime: Literal["cloud", "local"] = Field(default="cloud")
    model: str = Field(default="paraformer-v2")
    language: str = Field(default="en")
    base_url: str = Field(default=DEFAULT_WHISPER_BASE_URL)
    api_key: str = Field(default="")


class SubtitleJobOptions(BaseModel):
    enable_demucs: bool = Field(default=False)
    asr_profile: Literal["fast", "balanced", "accurate"] = Field(default="balanced")
    asr_fallback_enabled: bool = Field(default=True)
    asr_allow_cloud_fallback: bool = Field(default=True)
    asr_allow_local_fallback: bool = Field(default=True)
    enable_diarization: bool = Field(default=False)
    source_language: str = Field(default="en")
    target_language: str = Field(default="zh")
    llm: LlmOptions = Field(default_factory=LlmOptions)
    whisper: WhisperOptions = Field(default_factory=WhisperOptions)

    @model_validator(mode="after")
    def normalize_values(self) -> "SubtitleJobOptions":
        self.asr_profile = (self.asr_profile or "balanced").strip().lower() or "balanced"
        if self.asr_profile not in {"fast", "balanced", "accurate"}:
            self.asr_profile = "balanced"
        self.source_language = (self.source_language or "en").strip() or "en"
        self.target_language = (self.target_language or "zh").strip() or "zh"
        self.whisper.language = (self.whisper.language or self.source_language).strip() or self.source_language
        return self


class SubtitleJobFromUrlRequest(BaseModel):
    url: str = Field(default="")
    options: SubtitleJobOptions = Field(default_factory=SubtitleJobOptions)


class SourceUrlPolicyResult(BaseModel):
    normalized_url: str = ""
    host: str = ""
    allowed: bool = False
    reason: str = ""


class JobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    source_url_policy: Optional[SourceUrlPolicyResult] = None


class JobStageDetail(BaseModel):
    stage: str = ""
    step_key: str = ""
    step_label: str = ""
    done: int = 0
    total: int = 0
    unit: str = ""
    percent_in_stage: int = 0
    eta_seconds: Optional[int] = None
    updated_at: Optional[str] = None


class JobProgressEvent(BaseModel):
    event_id: str = ""
    stage: str = ""
    level: Literal["info", "success", "warning", "error"] = "info"
    message: str = ""
    percent: int = 0
    at: str = ""


class SubtitleSyncDiagnostics(BaseModel):
    alignment_quality_score: float = 0.0
    global_offset_ms: int = 0
    drift_scale: float = 1.0
    correction_applied: bool = False
    correction_method: str = "none"
    triggered: bool = False
    correction_score: float = 0.0


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress_percent: int = 0
    current_stage: str = "queued"
    message: str = ""
    error: Optional[str] = None
    error_code: str = ""
    partial_result: Optional[dict] = None
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    cancel_requested: bool = False
    whisper_runtime: str = ""
    whisper_model_requested: str = ""
    whisper_model_effective: str = ""
    asr_provider_effective: str = ""
    asr_fallback_used: bool = False
    queue_ahead: int = 0
    worker_alive: bool = True
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    stage_order: list[str] = Field(default_factory=list)
    total_duration_ms: int = 0
    stage_detail: Optional[JobStageDetail] = None
    recent_progress_events: list[JobProgressEvent] = Field(default_factory=list)
    status_revision: int = 0
    poll_interval_ms_hint: int = 800
    sync_diagnostics: Optional[SubtitleSyncDiagnostics] = None


class LegacySubtitle(BaseModel):
    id: int
    start: float
    end: float
    text: str
    translation: str = ""
    index: int


class WordSegment(BaseModel):
    id: int
    start: float
    end: float
    word: str
    confidence: Optional[float] = None
    asr_segment_index: int
    source: Literal["cloud", "local"]


class SubtitleJobResult(BaseModel):
    subtitles: list[LegacySubtitle]
    bilingual_srt: str
    source_srt: str
    stats: dict
    word_segments: list[WordSegment] = Field(default_factory=list)
    diagnostics: Optional[SubtitleSyncDiagnostics] = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "listening-subtitle-backend"
    version: str = "0.1.0"
    capabilities: dict[str, bool] = Field(
        default_factory=lambda: {
            "subtitle_jobs_conflict_409": True,
            "subtitle_job_status_whisper_fields": True,
            "subtitle_worker_watchdog": True,
            "subtitle_pipeline_v2": True,
            "subtitle_asr_fallback_chain": True,
            "subtitle_translation_refine_pass": True,
            "subtitle_perf_timing_metrics": True,
            "subtitle_whisper_local_models_probe": True,
        }
    )


class SubtitleConfigProbeResult(BaseModel):
    ok: bool = False
    message: str = ""
    detail: Optional[str] = None


class SubtitleConfigTestResponse(BaseModel):
    status: Literal["ok", "partial", "failed"] = "failed"
    llm: SubtitleConfigProbeResult
    whisper: SubtitleConfigProbeResult


class WhisperLocalModelStatus(BaseModel):
    model: str = ""
    installed: bool = False
    cache_path: str = ""


class WhisperLocalModelsResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    dependency_ok: bool = True
    message: str = ""
    models: list[WhisperLocalModelStatus] = Field(default_factory=list)


class BrowserErrorReportRequest(BaseModel):
    action: Literal["append", "clear"] = "append"
    file_name: str = Field(default="browser-error.log")
    content: str = Field(default="")


class BrowserErrorReportResponse(BaseModel):
    status: Literal["ok"] = "ok"
    action: Literal["append", "clear"]
    file_path: str


class BrowserErrorReadResponse(BaseModel):
    status: Literal["ok"] = "ok"
    file_path: str
    content: str = ""
    line_count: int = 0
    byte_size: int = 0


class SubtitleTaskMeta(BaseModel):
    pending_state: Literal["none", "failed", "cancelled"] = Field(default="none")
    last_job_id: str = Field(default="")
    last_job_status: JobStatus = Field(default="queued")
    last_stage: str = Field(default="")
    last_message: str = Field(default="")
    has_partial_result: bool = Field(default=False)
    source_mode: Literal["file", "url", "resume"] = Field(default="file")
    updated_at: int = Field(default=0)

    @model_validator(mode="after")
    def normalize(self) -> "SubtitleTaskMeta":
        self.pending_state = str(self.pending_state or "none").strip().lower()  # type: ignore[assignment]
        if self.pending_state not in {"none", "failed", "cancelled"}:
            self.pending_state = "none"  # type: ignore[assignment]
        self.last_job_id = str(self.last_job_id or "").strip()
        last_job_status = str(self.last_job_status or "queued").strip().lower()
        if last_job_status not in {"queued", "running", "completed", "failed", "cancelled"}:
            last_job_status = "queued"
        self.last_job_status = last_job_status  # type: ignore[assignment]
        self.last_stage = str(self.last_stage or "").strip()
        self.last_message = str(self.last_message or "").strip()
        self.has_partial_result = bool(self.has_partial_result)
        source_mode = str(self.source_mode or "file").strip().lower()
        if source_mode not in {"file", "url", "resume"}:
            source_mode = "file"
        self.source_mode = source_mode  # type: ignore[assignment]
        self.updated_at = max(0, int(self.updated_at or 0))
        return self


class HistoryRecord(BaseModel):
    videoName: str = Field(default="")
    srtName: str = Field(default="")
    currentIndex: int = Field(default=0)
    totalSentences: int = Field(default=0)
    thumbnail: str = Field(default="")
    timestamp: int = Field(default=0)
    completed: bool = Field(default=False)
    historyId: str = Field(default="")
    displayName: str = Field(default="")
    folderId: str = Field(default="")
    subtitleTaskMeta: Optional[SubtitleTaskMeta] = Field(default=None)

    @model_validator(mode="after")
    def normalize(self) -> "HistoryRecord":
        self.videoName = (self.videoName or "").strip()
        self.srtName = (self.srtName or "").strip()
        self.currentIndex = max(0, int(self.currentIndex or 0))
        self.totalSentences = max(0, int(self.totalSentences or 0))
        self.timestamp = max(0, int(self.timestamp or 0))
        self.thumbnail = str(self.thumbnail or "")
        self.historyId = str(self.historyId or "")
        self.displayName = str(self.displayName or "").strip()
        self.folderId = str(self.folderId or "").strip()
        self.completed = bool(self.completed)
        if self.subtitleTaskMeta and not self.subtitleTaskMeta.last_job_id:
            self.subtitleTaskMeta = None
        return self


class HistoryRecordsUpsertRequest(BaseModel):
    records: list[HistoryRecord] = Field(default_factory=list)


class HistoryRecordsResponse(BaseModel):
    records: list[HistoryRecord] = Field(default_factory=list)


class HistoryRecordsSyncResponse(BaseModel):
    status: Literal["ok"] = "ok"
    saved_count: int = 0
    records: list[HistoryRecord] = Field(default_factory=list)


class ReadingSourceSummary(BaseModel):
    video_name: str = Field(default="")
    srt_name: str = Field(default="")
    subtitle_count: int = Field(default=0)
    updated_at: int = Field(default=0)
    has_summary_terms: bool = Field(default=False)


class ReadingSourcesResponse(BaseModel):
    sources: list[ReadingSourceSummary] = Field(default_factory=list)


class LlmOptionsPublic(BaseModel):
    base_url: str = Field(default=DEFAULT_LLM_BASE_URL)
    model: str = Field(default="gpt-5.2")
    llm_support_json: bool = Field(default=False)
    api_key_masked: str = Field(default="")
    has_api_key: bool = Field(default=False)


class LlmOptionsUpdate(BaseModel):
    base_url: str = Field(default=DEFAULT_LLM_BASE_URL)
    model: str = Field(default="gpt-5.2")
    llm_support_json: bool = Field(default=False)


class ProfileSettings(BaseModel):
    english_level: Literal["junior", "senior", "cet4", "cet6", "kaoyan", "toefl", "sat"] = Field(default="cet4")
    english_level_numeric: float = Field(default=7.5)
    english_level_cefr: str = Field(default="B1")
    llm_mode: Literal["unified", "custom"] = Field(default="unified")
    llm_unified: LlmOptionsPublic = Field(default_factory=LlmOptionsPublic)
    llm_listening: LlmOptionsPublic = Field(default_factory=LlmOptionsPublic)
    llm_reading: LlmOptionsPublic = Field(default_factory=LlmOptionsPublic)
    updated_at: int = Field(default=0)

    @model_validator(mode="after")
    def normalize(self) -> "ProfileSettings":
        self.english_level = str(self.english_level or "cet4").strip().lower()  # type: ignore[assignment]
        if self.english_level not in {"junior", "senior", "cet4", "cet6", "kaoyan", "toefl", "sat"}:
            self.english_level = "cet4"  # type: ignore[assignment]
        self.llm_mode = str(self.llm_mode or "unified").strip().lower()  # type: ignore[assignment]
        if self.llm_mode not in {"unified", "custom"}:
            self.llm_mode = "unified"  # type: ignore[assignment]
        self.english_level_cefr = str(self.english_level_cefr or "").strip() or "B1"
        self.updated_at = max(0, int(self.updated_at or 0))
        return self


class ProfileSettingsUpdateRequest(BaseModel):
    english_level: Optional[Literal["junior", "senior", "cet4", "cet6", "kaoyan", "toefl", "sat"]] = None
    llm_mode: Optional[Literal["unified", "custom"]] = None
    llm_unified: Optional[LlmOptionsUpdate] = None
    llm_listening: Optional[LlmOptionsUpdate] = None
    llm_reading: Optional[LlmOptionsUpdate] = None


class ProfileKeysUpdateRequest(BaseModel):
    llm_unified_api_key: Optional[str] = None
    llm_listening_api_key: Optional[str] = None
    llm_reading_api_key: Optional[str] = None


class ProfileKeysUpdateResponse(BaseModel):
    status: Literal["ok"] = "ok"
    updated_fields: list[str] = Field(default_factory=list)


class AuthRegisterRequest(BaseModel):
    username: str = Field(default="", min_length=3, max_length=64)
    password: str = Field(default="", min_length=8, max_length=128)


class AuthLoginRequest(BaseModel):
    username: str = Field(default="", min_length=3, max_length=64)
    password: str = Field(default="", min_length=8, max_length=128)


class AuthUserResponse(BaseModel):
    user_id: str = ""
    username: str = ""
    created_at: int = 0


class AuthTokenResponse(BaseModel):
    token_type: Literal["bearer"] = "bearer"
    access_token: str = ""
    expires_at: int = 0
    user: AuthUserResponse = Field(default_factory=AuthUserResponse)


class AuthLogoutResponse(BaseModel):
    status: Literal["ok"] = "ok"


class WalletQuotaResponse(BaseModel):
    user_id: str = ""
    username: str = ""
    quota: int = 0
    used_quota: int = 0
    remaining_quota: int = 0
    request_count: int = 0


class WalletRedeemRequest(BaseModel):
    key: str = Field(default="", min_length=1, max_length=128)

    @model_validator(mode="after")
    def normalize(self) -> "WalletRedeemRequest":
        self.key = str(self.key or "").strip()
        return self


class WalletRedeemResponse(BaseModel):
    status: Literal["ok"] = "ok"
    added_quota: int = 0
    user_id: str = ""
    username: str = ""
    quota: int = 0
    used_quota: int = 0
    remaining_quota: int = 0
    request_count: int = 0


class WalletPackItem(BaseModel):
    id: str = ""
    label: str = ""
    price: float = 0.0
    quota: int = 0
    description: str = ""


class WalletPacksResponse(BaseModel):
    packs: list[WalletPackItem] = Field(default_factory=list)
    cost_multiplier: float = 3.0


class ReadingChoiceQuestion(BaseModel):
    question_id: str = Field(default="")
    question: str = Field(default="")
    choices: list[str] = Field(default_factory=list)
    answer_index: int = Field(default=0)
    explanation: str = Field(default="")


class ReadingShortQuestion(BaseModel):
    question_id: str = Field(default="")
    question: str = Field(default="")
    reference_answer: str = Field(default="")


class ReadingQuizPayload(BaseModel):
    choice_questions: list[ReadingChoiceQuestion] = Field(default_factory=list)
    short_questions: list[ReadingShortQuestion] = Field(default_factory=list)


class ReadingMaterialSlot(BaseModel):
    kind: Literal["intensive", "extensive"] = Field(default="intensive")
    text: str = Field(default="")
    word_count: int = Field(default=0)
    target_word_count: int = Field(default=0)
    generated: bool = Field(default=False)


class ReadingDifficultyReport(BaseModel):
    source_score: float = Field(default=0.0)
    source_level: float = Field(default=0.0)
    generated_level: float = Field(default=0.0)
    target_level: float = Field(default=0.0)
    gap_to_user: float = Field(default=0.0)
    recommended_ratio_preset: Literal["high_energy", "long_term", "low_energy"] = Field(default="long_term")
    hit_i_plus_one: bool = Field(default=False)
    used_cefr_fallback: bool = Field(default=False)
    detail: dict = Field(default_factory=dict)


class ReadingConfigSnapshot(BaseModel):
    scope: Literal["all", "intensive", "extensive"] = Field(default="all")
    ratio_preset: Literal["high_energy", "long_term", "low_energy"] = Field(default="long_term")
    difficulty_tier: Literal["very_easy", "easy", "balanced", "challenging", "hard"] = Field(default="balanced")
    genre: Literal["news", "science", "story", "workplace"] = Field(default="news")
    word_budget_total: int = Field(default=0)


class ReadingVersion(BaseModel):
    version_id: str = Field(default="")
    video_name: str = Field(default="")
    srt_name: str = Field(default="")
    user_level: str = Field(default="cet4")
    scope: Literal["all", "intensive", "extensive"] = Field(default="all")
    ratio_preset: Literal["high_energy", "long_term", "low_energy"] = Field(default="long_term")
    difficulty_tier: Literal["very_easy", "easy", "balanced", "challenging", "hard"] = Field(default="balanced")
    genre: Literal["news", "science", "story", "workplace"] = Field(default="news")
    i_plus_one_hit: bool = Field(default=False)
    pipeline_version: str = Field(default="reading_v2_v2")
    config: ReadingConfigSnapshot = Field(default_factory=ReadingConfigSnapshot)
    difficulty_report: ReadingDifficultyReport = Field(default_factory=ReadingDifficultyReport)
    materials: list[ReadingMaterialSlot] = Field(default_factory=list)
    quiz: ReadingQuizPayload = Field(default_factory=ReadingQuizPayload)
    created_at: int = Field(default=0)
    updated_at: int = Field(default=0)


class ReadingHistoryItem(BaseModel):
    version_id: str = Field(default="")
    video_name: str = Field(default="")
    srt_name: str = Field(default="")
    scope: Literal["all", "intensive", "extensive"] = Field(default="all")
    ratio_preset: Literal["high_energy", "long_term", "low_energy"] = Field(default="long_term")
    difficulty_tier: Literal["very_easy", "easy", "balanced", "challenging", "hard"] = Field(default="balanced")
    genre: Literal["news", "science", "story", "workplace"] = Field(default="news")
    i_plus_one_hit: bool = Field(default=False)
    created_at: int = Field(default=0)
    updated_at: int = Field(default=0)
    has_intensive: bool = Field(default=False)
    has_extensive: bool = Field(default=False)


class ReadingHistoryResponse(BaseModel):
    items: list[ReadingHistoryItem] = Field(default_factory=list)
    offset: int = Field(default=0)
    limit: int = Field(default=20)
    has_more: bool = Field(default=False)


class ReadingVersionResponse(BaseModel):
    version: ReadingVersion


class ReadingMaterialResponse(ReadingVersion):
    cached: bool = Field(default=False)


class ReadingMaterialGenerateRequest(BaseModel):
    video_name: str = Field(default="")
    srt_name: str = Field(default="")
    user_level: Literal["junior", "senior", "cet4", "cet6", "kaoyan", "toefl", "sat"] = Field(default="cet4")
    scope: Literal["all", "intensive", "extensive"] = Field(default="all")
    ratio_preset: Literal["high_energy", "long_term", "low_energy"] = Field(default="long_term")
    difficulty_tier: Literal["very_easy", "easy", "balanced", "challenging", "hard"] = Field(default="balanced")
    genre: Literal["news", "science", "story", "workplace"] = Field(default="news")
    force_regenerate: bool = Field(default=False)
    llm: Optional[LlmOptions] = None

    @model_validator(mode="after")
    def normalize(self) -> "ReadingMaterialGenerateRequest":
        self.video_name = (self.video_name or "").strip()
        self.srt_name = (self.srt_name or "").strip()
        self.user_level = str(self.user_level or "cet4").strip().lower()  # type: ignore[assignment]
        if self.user_level not in {"junior", "senior", "cet4", "cet6", "kaoyan", "toefl", "sat"}:
            self.user_level = "cet4"  # type: ignore[assignment]
        self.scope = str(self.scope or "all").strip().lower()  # type: ignore[assignment]
        if self.scope not in {"all", "intensive", "extensive"}:
            self.scope = "all"  # type: ignore[assignment]
        self.ratio_preset = str(self.ratio_preset or "long_term").strip().lower()  # type: ignore[assignment]
        if self.ratio_preset not in {"high_energy", "long_term", "low_energy"}:
            self.ratio_preset = "long_term"  # type: ignore[assignment]
        self.difficulty_tier = str(self.difficulty_tier or "balanced").strip().lower()  # type: ignore[assignment]
        if self.difficulty_tier not in {"very_easy", "easy", "balanced", "challenging", "hard"}:
            self.difficulty_tier = "balanced"  # type: ignore[assignment]
        self.genre = str(self.genre or "news").strip().lower()  # type: ignore[assignment]
        if self.genre not in {"news", "science", "story", "workplace"}:
            self.genre = "news"  # type: ignore[assignment]
        return self


class ReadingShortAnswerSubmitRequest(BaseModel):
    version_id: str = Field(default="")
    question_id: str = Field(default="")
    answer_text: str = Field(default="")

    @model_validator(mode="after")
    def normalize(self) -> "ReadingShortAnswerSubmitRequest":
        self.version_id = str(self.version_id or "").strip()
        self.question_id = str(self.question_id or "").strip()
        self.answer_text = str(self.answer_text or "").strip()
        return self


class ReadingScoreDimension(BaseModel):
    name: str = Field(default="")
    score: float = Field(default=0.0)
    max_score: float = Field(default=5.0)
    comment: str = Field(default="")


class ReadingShortAnswerSubmitResponse(BaseModel):
    attempt_id: str = Field(default="")
    version_id: str = Field(default="")
    question_id: str = Field(default="")
    answer_text: str = Field(default="")
    total_score: float = Field(default=0.0)
    max_score: float = Field(default=20.0)
    dimensions: list[ReadingScoreDimension] = Field(default_factory=list)
    overall_comment: str = Field(default="")
    reference_answer: str = Field(default="")
    submitted_at: int = Field(default=0)


class ReadingShortAnswerHistoryItem(BaseModel):
    attempt_id: str = Field(default="")
    version_id: str = Field(default="")
    question_id: str = Field(default="")
    answer_text: str = Field(default="")
    total_score: float = Field(default=0.0)
    max_score: float = Field(default=20.0)
    dimensions: list[ReadingScoreDimension] = Field(default_factory=list)
    overall_comment: str = Field(default="")
    reference_answer: str = Field(default="")
    submitted_at: int = Field(default=0)


class ReadingShortAnswerHistoryResponse(BaseModel):
    items: list[ReadingShortAnswerHistoryItem] = Field(default_factory=list)


class ReadingShortAnswerHistoryDeleteRequest(BaseModel):
    version_id: str = Field(default="")
    question_id: str = Field(default="")

    @model_validator(mode="after")
    def normalize(self) -> "ReadingShortAnswerHistoryDeleteRequest":
        self.version_id = str(self.version_id or "").strip()
        self.question_id = str(self.question_id or "").strip()
        return self


class DeleteOperationResponse(BaseModel):
    status: Literal["ok"] = "ok"
    deleted_count: int = 0
