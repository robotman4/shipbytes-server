"""Versioned content transport, separate from the interactive publishing API."""
from datetime import date
from typing import Literal
from pydantic import Field, field_validator
from .schemas import IssueInput, StoryInput

class RepositoryIssue(IssueInput):
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
