from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Essay(Base):
    __tablename__ = "essays"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(250))
    essay_text: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    analysis: Mapped["EssayAnalysis"] = relationship(back_populates="essay", cascade="all, delete-orphan", uselist=False)
    work: Mapped["EssayWork | None"] = relationship(cascade="all, delete-orphan", uselist=False, foreign_keys="EssayWork.essay_id")


class EssayAnalysis(Base):
    __tablename__ = "essay_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    essay_id: Mapped[int] = mapped_column(ForeignKey("essays.id", ondelete="CASCADE"), unique=True)
    essay_type: Mapped[str] = mapped_column(String(80))
    essay_style: Mapped[str] = mapped_column(String(80))
    structure_score: Mapped[int] = mapped_column(Integer)
    argumentation_score: Mapped[int] = mapped_column(Integer)
    logic_score: Mapped[int] = mapped_column(Integer)
    evidence_score: Mapped[int] = mapped_column(Integer)
    language_score: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[int] = mapped_column(Integer)
    analysis_result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    essay: Mapped[Essay] = relationship(back_populates="analysis")


class EssayWork(Base):
    """Additive table: old essays/analyses and their schema remain intact."""
    __tablename__ = "essay_workflows"
    essay_id: Mapped[int] = mapped_column(ForeignKey("essays.id", ondelete="CASCADE"), primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("essays.id", ondelete="SET NULL"), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[dict] = mapped_column(JSONB)
