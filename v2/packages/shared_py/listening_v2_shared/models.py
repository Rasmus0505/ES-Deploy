from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = 'users'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Session(Base):
    __tablename__ = 'sessions'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    token_jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WalletAccount(Base):
    __tablename__ = 'wallet_accounts'

    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    balance_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class WalletLedger(Base):
    __tablename__ = 'wallet_ledger'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    delta_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RedeemCode(Base):
    __tablename__ = 'redeem_codes'

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='active', index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default='admin')
    redeemed_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    redeemed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelRoute(Base):
    __tablename__ = 'model_routes'

    model_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cost_per_unit: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0.0)
    multiplier: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=1.0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class MediaAsset(Base):
    __tablename__ = 'media_assets'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(8), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default='')
    local_path: Mapped[str] = mapped_column(Text, nullable=False, default='')
    object_key: Mapped[str] = mapped_column(Text, nullable=False, default='')
    duration_seconds: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessingJob(Base):
    __tablename__ = 'processing_jobs'

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey('media_assets.id', ondelete='SET NULL'), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='queued', index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default='queued')
    model_asr: Mapped[str] = mapped_column(String(64), nullable=False, default='paraformer-v2')
    model_mt: Mapped[str] = mapped_column(String(64), nullable=False, default='qwen-mt')
    queue_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default='')
    exercise_set_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AsrSegment(Base):
    __tablename__ = 'asr_segments'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('processing_jobs.job_id', ondelete='CASCADE'), nullable=False, index=True)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript_en: Mapped[str] = mapped_column(Text, nullable=False)
    translation_zh: Mapped[str] = mapped_column(Text, nullable=False, default='')
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint('job_id', 'segment_index', name='uq_asr_segment_job_index'),)


class ExerciseSet(Base):
    __tablename__ = 'exercise_sets'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('processing_jobs.job_id', ondelete='CASCADE'), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default='Listening Exercise')
    source_lang: Mapped[str] = mapped_column(String(8), nullable=False, default='en')
    target_lang: Mapped[str] = mapped_column(String(8), nullable=False, default='zh')
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ExerciseItem(Base):
    __tablename__ = 'exercise_items'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    exercise_set_id: Mapped[str] = mapped_column(ForeignKey('exercise_sets.id', ondelete='CASCADE'), nullable=False, index=True)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript_en: Mapped[str] = mapped_column(Text, nullable=False)
    translation_zh: Mapped[str] = mapped_column(Text, nullable=False, default='')
    audio_local_path: Mapped[str] = mapped_column(Text, nullable=False, default='')
    audio_clip_key: Mapped[str] = mapped_column(Text, nullable=False, default='')
    words_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    accepted_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint('exercise_set_id', 'segment_index', name='uq_exercise_item_set_index'),)


class ExerciseAttempt(Base):
    __tablename__ = 'exercise_attempts'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    exercise_item_id: Mapped[str] = mapped_column(ForeignKey('exercise_items.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    submitted_words_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LearningProgress(Base):
    __tablename__ = 'learning_progress'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    exercise_set_id: Mapped[str] = mapped_column(ForeignKey('exercise_sets.id', ondelete='CASCADE'), nullable=False, index=True)
    last_segment_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint('user_id', 'exercise_set_id', name='uq_learning_progress_user_set'),)


class DeletionRequest(Base):
    __tablename__ = 'deletion_requests'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='pending')
    note: Mapped[str] = mapped_column(Text, nullable=False, default='')
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index('idx_processing_jobs_user_created', ProcessingJob.user_id, ProcessingJob.created_at.desc())
Index('idx_wallet_ledger_user_created', WalletLedger.user_id, WalletLedger.created_at.desc())
