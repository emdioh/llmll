"""Plain data returned by the NLP layer."""

from pydantic import BaseModel, Field


class LTMatch(BaseModel):
    """One LanguageTool match; `offset`/`length` are character positions in the checked text."""

    offset: int
    length: int
    rule_id: str
    category: str
    message: str
    replacements: list[str] = Field(default_factory=list)
