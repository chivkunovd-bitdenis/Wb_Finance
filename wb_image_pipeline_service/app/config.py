from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="WIP_",
    )

    env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 9100

    redis_url: str = "redis://127.0.0.1:6379/1"
    database_url: str = "sqlite:////tmp/wip_jobs.db"
    media_root: str = "/data/media"

    openai_api_key: str = ""
    openai_model_structure: str = "gpt-4.1-mini"
    openai_model_prompt_pack: str = "gpt-4.1"
    openai_image_model: str = "gpt-image-1"

    internal_hmac_secret: str = "dev-insecure-secret"
    monolith_base_url: str = "http://api:8000"
    monolith_reference_secret: str = ""


settings = Settings()
