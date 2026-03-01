from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_env: str = Field(default='development', alias='APP_ENV')
    app_name: str = Field(default='listening-v2', alias='APP_NAME')
    database_url: str = Field(default='postgresql+psycopg://postgres:postgres@localhost:5432/listening_v2', alias='DATABASE_URL')
    redis_url: str = Field(default='redis://localhost:6379/0', alias='REDIS_URL')

    jwt_secret: str = Field(default='change-me', alias='JWT_SECRET')
    jwt_exp_minutes: int = Field(default=60 * 24 * 7, alias='JWT_EXP_MINUTES')

    admin_api_token: str = Field(default='change-admin-token', alias='ADMIN_API_TOKEN')
    app_master_key: str = Field(default='change-master-key', alias='APP_MASTER_KEY')

    dashscope_api_key: str = Field(default='', alias='DASHSCOPE_API_KEY')
    dashscope_base_url: str = Field(default='https://dashscope.aliyuncs.com/compatible-mode/v1', alias='DASHSCOPE_BASE_URL')

    oss_access_key_id: str = Field(default='', alias='OSS_ACCESS_KEY_ID')
    oss_access_key_secret: str = Field(default='', alias='OSS_ACCESS_KEY_SECRET')
    oss_bucket: str = Field(default='', alias='OSS_BUCKET')
    oss_endpoint: str = Field(default='', alias='OSS_ENDPOINT')

    runtime_dir: str = Field(default='./runtime', alias='RUNTIME_DIR')
    max_video_minutes: int = Field(default=20, alias='MAX_VIDEO_MINUTES')
    keep_source_hours: int = Field(default=24, alias='KEEP_SOURCE_HOURS')
    keep_intermediate_hours: int = Field(default=72, alias='KEEP_INTERMEDIATE_HOURS')
    auto_init_db: bool = Field(default=False, alias='AUTO_INIT_DB')
    enable_metrics: bool = Field(default=True, alias='ENABLE_METRICS')
    worker_metrics_port: int = Field(default=9101, alias='WORKER_METRICS_PORT')

    enable_mock_pipeline: bool = Field(default=True, alias='ENABLE_MOCK_PIPELINE')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
