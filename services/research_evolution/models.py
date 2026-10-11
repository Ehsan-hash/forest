from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from services.api.db import Base, Identity


class GoalContractVersion(Identity, Base):
    __tablename__ = 'goal_contract_versions'
    __table_args__ = (UniqueConstraint('project_id', 'revision'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_revision: Mapped[int] = mapped_column(Integer)
    objective: Mapped[str] = mapped_column(Text)
    success_criteria: Mapped[list] = mapped_column(JSON, default=list)
    contract: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_run_ids: Mapped[list] = mapped_column(JSON, default=list)
    affected_branch_ids: Mapped[list] = mapped_column(JSON, default=list)


class EvaluationProtocolVersion(Identity, Base):
    __tablename__ = 'evaluation_protocol_versions'
    __table_args__ = (UniqueConstraint('project_id', 'protocol_id', 'revision'),)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    protocol_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(240))
    definition: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    feedback_run_ids: Mapped[list] = mapped_column(JSON, default=list)
    classification: Mapped[str] = mapped_column(String(40), default='exploratory')
