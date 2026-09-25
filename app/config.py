from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://outreach:change-me@localhost:5432/outreach'
    app_secret: str = 'change-me'
    admin_username: str = 'admin'
    admin_password: str = 'change-me'
    company_name: str = 'Reinigingsdokter'
    company_website: str = 'https://www.reinigingsdokter.nl'
    company_address: str = ''
    company_email: str = ''
    dry_run: bool = True
    daily_email_limit: int = 25
    hourly_email_limit: int = 5
    min_delay_seconds: int = 60
    max_delay_seconds: int = 180
    max_followups: int = 1
    min_lead_score: int = 65
    service_regions: str = 'Zuid-Holland'
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    gmail_access_token: str = ''
    gmail_client_id: str = ''
    gmail_client_secret: str = ''
    gmail_refresh_token: str = ''
    openai_api_key: str = ''
    openai_model: str = 'gpt-4.1-mini'
    discovery_api_url: str = ''
    discovery_api_key: str = ''
    discovery_api_terms_accepted: bool = False
    enable_automation: bool = False
    live_send_enabled: bool = False
    imap_host: str = ''
    imap_username: str = ''
    imap_password: str = ''


@lru_cache
def settings() -> Settings:
    return Settings()
