"""Versioned content transport, separate from the interactive publishing API."""
from datetime import date
from typing import Literal
from pathlib import PurePosixPath
from pydantic import BaseModel, ConfigDict, HttpUrl, Field, field_validator
from .schemas import IssueInput, StoryInput

class PublicationImage(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    path: str = Field(min_length=1, max_length=300)
    alt: str = Field(min_length=1, max_length=500)
    credit: str = Field(default='', max_length=300)
    source_url: HttpUrl | Literal[''] = ''
    usage: str = Field(default='', max_length=1000)

    @field_validator('path')
    @classmethod
    def asset_path(cls, value):
        path = PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or not value.startswith('assets/') or '\\' in value or path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
            raise ValueError('Image must reference a JPEG, PNG or WebP under assets/')
        return value

class RepositoryIssue(IssueInput):
    image: PublicationImage | None = None
    schema_version: Literal[1]
    published_date: date
    stories: list[StoryInput] = Field(min_length=1, max_length=20)

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
