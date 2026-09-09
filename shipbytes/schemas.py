from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

Slug = Annotated[str, Field(min_length=1, max_length=120, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')]
Short = Annotated[str, Field(min_length=1, max_length=300)]

class StoryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid', from_attributes=True)
    slug: Slug
    title: Short
    byte: str = Field(min_length=1, max_length=1500)
    summary: str = Field(min_length=1, max_length=30000)
    why_it_matters: str = Field(min_length=1, max_length=2000)
    source_name: str = Field(default='', max_length=300)
    source_url: HttpUrl | Literal[''] = ''
    source_type: Literal['external', 'shipbytes'] = 'external'
    published_at: datetime | None = None

    @model_validator(mode='after')
    def source(self):
        if self.source_type == 'external' and (not self.source_name or not self.source_url):
            raise ValueError('External stories require source_name and an HTTP(S) source_url')
        return self

class IssueInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    slug: Slug
    title: Short
    subject: Short
    intro: str = Field(default='', max_length=5000)
    stories: list[StoryInput] = Field(max_length=20)

    @field_validator('subject', mode='before')
    @classmethod
    def plain_subject(cls, value):
        return value.replace('—', '-') if isinstance(value, str) else value

    @model_validator(mode='after')
    def unique_stories(self):
        if len({s.slug for s in self.stories}) != len(self.stories):
            raise ValueError('Story slugs must be unique')
        return self
