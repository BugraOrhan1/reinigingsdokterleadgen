from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables / `.env`.

    Credentials are never hardcoded here. Sensitive values are kept as plain
    strings for simplicity but are never written to logs or the database.
    """

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    database_url: str = 'postgresql+psycopg://outreach:change-me@localhost:5432/outreach'
    app_secret: str = 'change-me'
    admin_username: str = 'admin'
    admin_password: str = 'change-me'
    cookie_secure: bool = False
    session_max_age_hours: int = 12

    company_name: str = 'Reinigingsdokter'
    company_website: str = 'https://www.reinigingsdokter.nl'
    company_address: str = ''
    company_email: str = ''

    # Sending behaviour — conservative defaults.
    dry_run: bool = True
    live_send_enabled: bool = False
    enable_automation: bool = False
    daily_email_limit: int = 25
    hourly_email_limit: int = 5
    min_delay_seconds: int = 60
    max_delay_seconds: int = 180
    max_followups: int = 1
    min_lead_score: int = 65
    followup_delay_days: int = 7
    min_contact_confidence: int = 60
    service_regions: str = 'Zuid-Holland'

    # Business contact rules.
    general_contact_localparts: str = 'info,contact,office,sales,administratie,onfo,klantenservice,receptie,planning,facturen'

    # SMTP (only needed for live sending).
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_from: str = ''

    # Gmail API via OAuth refresh token (alternative to SMTP).
    gmail_access_token: str = ''
    gmail_client_id: str = ''
    gmail_client_secret: str = ''
    gmail_refresh_token: str = ''

    # IMAP inbox for reading replies / bounces.
    imap_host: str = ''
    imap_username: str = ''
    imap_password: str = ''

    # LLM (optional; a deterministic Dutch template is the fallback).
    openai_api_key: str = ''
    openai_model: str = 'gpt-4.1-mini'

    # Discovery — OpenStreetMap Overpass / Nominatim (attributed, rate-limited).
    discovery_api_url: str = ''
    discovery_api_key: str = ''
    discovery_api_terms_accepted: bool = False
    nominatim_user_agent: str = 'reinigingsdokter-leadgen/1.0 (contact: via company email)'
    max_pages_per_site: int = 5
    request_timeout_seconds: int = 10
    request_user_agent: str = 'ReinigingsdokterLeadResearch/1.0 (public pages only)'

    @property
    def insecure_defaults(self) -> bool:
        """True while placeholder credentials are still in use.

        The dashboard refuses to serve/send while this is True, forcing the
        operator to configure real credentials before use.
        """
        return self.app_secret in ('', 'change-me') or self.admin_password in ('', 'change-me')

    @property
    def smtp_from_address(self) -> str:
        return self.smtp_from or self.company_email

    @property
    def has_gmail(self) -> bool:
        return bool(self.gmail_refresh_token and self.gmail_client_id and self.gmail_client_secret)

    @property
    def has_smtp(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.smtp_password)

    def live_sending_configured(self) -> bool:
        return bool(self.company_address and self.company_email and (self.has_smtp or self.has_gmail))


@lru_cache
def settings() -> Settings:
    return Settings()
