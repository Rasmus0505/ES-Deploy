from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RedeemRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)
    idempotency_key: str = Field(min_length=8, max_length=128)


class SubmitAttemptRequest(BaseModel):
    item_id: str
    submitted_words: list[str]


class CreateRedeemCodesRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=1000)
    credits: int = Field(default=1000, ge=1)
    expires_days: int = Field(default=30, ge=1, le=365)
    prefix: str = Field(default='V2')


class ModelRoutePatchItem(BaseModel):
    model_name: str
    enabled: bool
    cost_per_unit: float = Field(ge=0)
    multiplier: float = Field(ge=0)


class PatchModelRoutesRequest(BaseModel):
    items: list[ModelRoutePatchItem]
