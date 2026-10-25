import re
from uuid import UUID
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Quote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def word_count(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:[’'–-][^\W_]+)*", text, re.UNICODE))


class EssayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Тақырыпсыз эссе", max_length=250)
    essay_text: str = Field(min_length=1, max_length=30000)
    parent_id: int | None = Field(default=None, gt=0)
    request_id: UUID | None = None

    @field_validator("essay_text", "title")
    @classmethod
    def clean(cls, value: str) -> str:
        value = value.strip()
        if not value or not word_count(value):
            raise ValueError("Мәтін бос болмауы керек.")
        return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Criterion(StrictModel):
    score: int = Field(ge=0)
    explanation: Text
    quotes: list[Quote] = Field(max_length=8)
    weaknesses: list[Text] = Field(max_length=8)
    recommendations: list[Text] = Field(max_length=8)


class StructureCriterion(Criterion):
    score: int = Field(ge=0, le=25)


class ArgumentationCriterion(Criterion):
    score: int = Field(ge=0, le=30)


class LogicCriterion(Criterion):
    score: int = Field(ge=0, le=20)


class EvidenceCriterion(Criterion):
    score: int = Field(ge=0, le=15)


class LanguageCriterion(Criterion):
    score: int = Field(ge=0, le=10)


class Scores(StrictModel):
    structure: StructureCriterion
    argumentation: ArgumentationCriterion
    logic: LogicCriterion
    evidence: EvidenceCriterion
    language: LanguageCriterion


class Classification(StrictModel):
    style: Literal["Ғылыми", "Публицистикалық", "Көркем", "Анықталмады"]
    essay_type: Literal["Аргументативті", "Аналитикалық", "Түсіндірмелі", "Салыстырмалы", "Субъективті", "Объективті", "Анықталмады"]
    explanation: Text
    quotes: list[Quote] = Field(max_length=8)


class StructureMap(StrictModel):
    introduction: list[Quote] = Field(max_length=8)
    thesis: list[Quote] = Field(max_length=8)
    arguments: list[Quote] = Field(max_length=12)
    evidence: list[Quote] = Field(max_length=12)
    counterargument: list[Quote] = Field(max_length=8)
    conclusion: list[Quote] = Field(max_length=8)


class Correction(StrictModel):
    original: Quote
    corrected: Text
    explanation: Text


class TextFeedback(StrictModel):
    category: Literal["structure", "argumentation", "logic", "evidence", "language"]
    quote: Quote
    explanation: Text
    suggestion: Text


class TopicAlignment(StrictModel):
    status: Literal["matched", "partial", "mismatched", "undetermined"]
    explanation: Text
    quotes: list[Quote] = Field(max_length=4)
    suggested_title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)] | None

    @model_validator(mode="after")
    def check_consistency(self):
        if self.status in ("partial", "mismatched"):
            if not self.suggested_title or not word_count(self.suggested_title):
                raise ValueError("A useful alternative title is required")
        elif self.suggested_title is not None:
            raise ValueError("No replacement title for matched or undetermined results")
        if self.status != "undetermined" and not self.quotes:
            raise ValueError("Topic decisions require evidence from the essay")
        return self


class TopicSelection(StrictModel):
    """Internal AI format: the server resolves evidence IDs to exact source text."""
    status: Literal["matched", "partial", "mismatched", "undetermined"]
    explanation: Text
    evidence_ids: list[int] = Field(min_length=1, max_length=4)
    suggested_title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)] | None


class AIAnalysis(StrictModel):
    summary: Text
    strengths: list[Text] = Field(max_length=8)
    weaknesses: list[Text] = Field(max_length=8)
    recommendations: list[Text] = Field(max_length=8)
    classification: Classification
    criteria: Scores
    structure_map: StructureMap
    language_corrections: list[Correction] = Field(max_length=12)
    text_feedback: list[TextFeedback] = Field(max_length=20)


MAX_SCORES = {"structure": 25, "argumentation": 30, "logic": 20, "evidence": 15, "language": 10}


class SourceQuoteError(ValueError):
    def __init__(self, quotes: list[str]):
        self.invalid_quotes = quotes
        super().__init__("AI returned a quotation absent from the essay")


def validate_quotes(analysis: AIAnalysis, essay: str) -> None:
    """Reject invented quotations; whitespace normalization allows paragraph wrapping."""
    quotes = list(analysis.classification.quotes)
    for criterion in analysis.criteria.model_dump().values():
        quotes.extend(criterion["quotes"])
    for entries in analysis.structure_map.model_dump().values():
        quotes.extend(entries)
    quotes.extend(item.original for item in analysis.language_corrections)
    quotes.extend(item.quote for item in analysis.text_feedback)
    validate_source_quotes(quotes, essay)


def validate_source_quotes(quotes: list[str], essay: str) -> None:
    normalize = lambda text: " ".join(text.split())
    source = normalize(essay)
    invalid = [quote for quote in quotes if normalize(quote) not in source]
    if invalid:
        raise SourceQuoteError(list(dict.fromkeys(invalid)))
