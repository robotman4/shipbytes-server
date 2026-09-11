"""Versioned content transport, separate from the interactive publishing API."""
from datetime import date
from typing import Literal
from pathlib import PurePosixPath
from pydantic import BaseModel, ConfigDict, HttpUrl, Field, field_validator, model_validator
from .schemas import IssueInput, StoryInput

from .assets import folder_identity

class PublicationImage(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    provider: Literal['repository', 'dropbox'] = 'repository'
    shared_folder_url: HttpUrl | None = None
    sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    path: str = Field(min_length=1, max_length=300)
    type: Literal['generated', 'licensed', 'source', 'own']
    alt: str = Field(min_length=1, max_length=500)
    credit: str = Field(default='', max_length=300)
    source_url: HttpUrl | None = None
    usage: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def provenance(self):
        if self.provider == 'dropbox':
            if not self.shared_folder_url:
                raise ValueError('Dropbox images require shared_folder_url')
            folder_identity(self.shared_folder_url)
        elif not self.path.startswith('assets/') or self.shared_folder_url:
            raise ValueError('Repository images require assets/ paths and no shared_folder_url')
        if self.type == 'source' and (not self.source_url or not self.usage):
            raise ValueError('Source images require source_url and usage')
        if self.type == 'generated':
            self.credit = self.credit or 'Ship Bytes'
            self.usage = self.usage or 'generated'
        return self

    @field_validator('path')
    @classmethod
    def asset_path(cls, value):
        path = PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or ':' in value or '%' in value or any(part in ('', '.') for part in value.split('/')) or '\\' in value or path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
            raise ValueError('Image must reference a relative JPEG, PNG or WebP path')
        return value

class RepositoryStory(StoryInput):
    image: PublicationImage | None = None

class RepositoryIssue(IssueInput):
    image: PublicationImage | None = None
    schema_version: Literal[1]
    published_date: date
    stories: list[RepositoryStory] = Field(min_length=1, max_length=20)

    @field_validator('schema_version', mode='before')
    @classmethod
    def version_is_integer(cls, value):
        if type(value) is not int:
            raise ValueError('schema_version must be integer 1')
        return value

    @field_validator('published_date', mode='before')
    @classmethod
    def iso_date(cls, value):
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError('published_date must be YYYY-MM-DD')
        return date.fromisoformat(value)
