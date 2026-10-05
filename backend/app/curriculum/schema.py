"""Pydantic models validating the curriculum YAML files (design: docs/design/M1.md §1)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
Level = Literal["A1", "A2", "B1", "B2", "C1", "C2"]
Pos = Literal[
    "noun", "verb", "adj", "adv", "prep", "conj", "pron", "det", "num", "particle", "phrase"
]
Gender = Literal["m", "f", "n"]

LEX_ID = r"^lex:[a-z0-9-]+(#[a-z0-9-]+)?$"
GRAM_ID = r"^gram:[a-z0-9-]+$"
CX_ID = r"^cx:[a-z0-9-]+$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Translations(Strict):
    it: str = Field(min_length=1)
    en: str = Field(min_length=1)


class Example(Strict):
    de: str = Field(min_length=1)
    it: str = Field(min_length=1)


class VerbInfo(Strict):
    aux: Literal["haben", "sein"] | None = None
    separable: bool | None = None
    irregular: bool | None = None
    praeteritum: str | None = None
    partizip: str | None = None


class FalseFriend(Strict):
    lang: Literal["en", "it"]
    word: str
    note_it: str


class Interference(Strict):
    it_gender: Gender | None = None
    false_friend: FalseFriend | None = None
    note_it: str | None = None


class LexiconEntry(Strict):
    id: str = Field(pattern=LEX_ID)
    lemma: str = Field(min_length=1)
    pos: Pos
    gender: Gender | None = None
    plural: str | None = None
    plural_only: bool = False
    level: Level
    translations: Translations
    example: Example | None = None
    verb: VerbInfo | None = None
    prep_case: Literal["acc", "dat", "gen", "acc_dat"] | None = None
    interference: Interference | None = None
    requires: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_pos_specific(self) -> "LexiconEntry":
        if self.pos == "noun":
            if self.gender is None and not self.plural_only:
                raise ValueError("nouns require `gender` unless `plural_only` is true")
        else:
            if self.gender is not None or self.plural is not None or self.plural_only:
                raise ValueError("`gender`, `plural` and `plural_only` are for nouns only")
        if self.verb is not None and self.pos != "verb":
            raise ValueError("`verb` is for verbs only")
        if self.prep_case is not None and self.pos != "prep":
            raise ValueError("`prep_case` is for prepositions only")
        return self


class GrammarEntry(Strict):
    id: str = Field(pattern=GRAM_ID)
    title_it: str = Field(min_length=1)
    title_en: str = Field(min_length=1)
    level: Level
    requires: list[str] = Field(default_factory=list)
    diagnostic_tags: dict[str, list[str]] = Field(default_factory=dict)
    reference_it: str = Field(min_length=1)
    examples: list[Example] = Field(min_length=2)

    @field_validator("diagnostic_tags", mode="before")
    @classmethod
    def _stringify_tag_values(cls, value: object) -> object:
        # YAML turns bare `true`/`false`/`yes`/`no` into booleans; tags are always strings.
        if isinstance(value, dict):
            return {
                k: [str(v).lower() if isinstance(v, bool) else v for v in vs]
                if isinstance(vs, list)
                else vs
                for k, vs in value.items()
            }
        return value


class ConstructionEntry(Strict):
    id: str = Field(pattern=CX_ID)
    pattern: str = Field(min_length=1)
    level: Level
    translations: Translations
    example: Example
    requires: list[str] = Field(default_factory=list)
