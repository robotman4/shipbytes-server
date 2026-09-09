from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    site_url: str = 'http://localhost:8000'
    site_name: str = 'Ship Bytes'
    database_url: str = 'sqlite:///./shipbytes.db'
    secret_key: str = 'development-only-change-before-deploying'
    publish_api_token: str = ''
    email_backend: Literal['development', 'resend'] = 'development'
    environment: Literal['development', 'production'] = 'development'
    email_directory: str = './dev-emails'
    media_directory: str = ''
    resend_api_key: str = ''
    resend_from_email: str = ''
    resend_from_name: str = 'Ship Bytes'
    resend_segment_id: str = ''
    resend_webhook_secret: str = ''

    @model_validator(mode='after')
    def validate_config(self):
        self.site_url = self.site_url.rstrip('/')
        url = urlsplit(self.site_url)
        if url.scheme not in ('http', 'https') or not url.netloc or url.path or url.query or url.fragment:
            raise ValueError('SITE_URL must be an HTTP(S) origin')
        if self.environment == 'production':
            if url.scheme != 'https' or len(self.secret_key) < 32 or self.secret_key.startswith('development'):
                raise ValueError('Production requires HTTPS and a random SECRET_KEY')
            if len(self.publish_api_token) < 32 or self.email_backend != 'resend':
                raise ValueError('Production requires a strong publishing token and Resend')
        if self.email_backend == 'resend' and not all((self.resend_api_key, self.resend_from_email, self.resend_segment_id, self.resend_webhook_secret)):
            raise ValueError('Configure all Resend settings')
        return self

@lru_cache
def settings():
    return Settings()
