from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PT_")

    database_url: str = "sqlite:///./portfolio.db"
    base_currency: str = "ZAR"
    risk_free_rate: float = 0.08  # annual, used for the Sharpe ratio (approx. SA 3-month T-bill)
    load_sample_data: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
