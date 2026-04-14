from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .engine import db


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="organizations_pkey"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_name: Mapped[str] = mapped_column(Text, nullable=False)
    org_level: Mapped[int] = mapped_column(Integer, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    division_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    department_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    orgcd1: Mapped[str | None] = mapped_column(Text, nullable=True)
    orgcd2: Mapped[str | None] = mapped_column(Text, nullable=True)
    orgcd3: Mapped[str | None] = mapped_column(Text, nullable=True)
    orgcd4: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @classmethod
    def get_teams_by_org(cls, org_name: str):
        """Get all team names that belong to an organization (any level)."""
        return db.session.query(cls.team_name).filter(
            cls.team_name.isnot(None),
            db.or_(
                cls.company_name == org_name,
                cls.division_name == org_name,
                cls.department_name == org_name,
                cls.team_name == org_name,
            )
        ).distinct().all()

    @classmethod
    def get_org_chain_for_team(cls, team_name: str):
        """Get the full org chain for a team: [team, department, division, company]."""
        org = db.session.query(cls).filter(cls.team_name == team_name, cls.org_level == 4).first()
        if not org:
            return []
        chain = []
        if org.team_name:
            chain.append(org.team_name)
        if org.department_name:
            chain.append(org.department_name)
        if org.division_name:
            chain.append(org.division_name)
        if org.company_name:
            chain.append(org.company_name)
        return chain

    @classmethod
    def search_orgs(cls, keyword: str = "", exclude_level: int = None):
        """Search organizations by name keyword. Returns distinct org entries."""
        query = db.session.query(cls.org_name, cls.org_level).distinct()
        if keyword:
            query = query.filter(cls.org_name.ilike(f"%{keyword}%"))
        if exclude_level:
            query = query.filter(cls.org_level != exclude_level)
        return query.order_by(cls.org_level, cls.org_name).all()

    @classmethod
    def get_tree_rows(cls, keyword: str = ""):
        """Return org rows for tree rendering: (id, org_name, org_level, parent_id).

        When keyword is empty, returns the entire tree.
        When keyword is given, returns matched rows plus their full ancestor chain
        so the tree remains connected (no orphans).
        """
        base_cols = (cls.id, cls.org_name, cls.org_level, cls.parent_id)

        if not keyword:
            return (
                db.session.query(*base_cols)
                .order_by(cls.org_level, cls.org_name)
                .all()
            )

        matched = (
            db.session.query(*base_cols)
            .filter(cls.org_name.ilike(f"%{keyword}%"))
            .all()
        )
        if not matched:
            return []

        included: dict[str, tuple] = {r.id: r for r in matched}

        # Walk up ancestors to keep the tree connected.
        pending = {r.parent_id for r in matched if r.parent_id}
        while pending:
            parents = (
                db.session.query(*base_cols)
                .filter(cls.id.in_(pending))
                .all()
            )
            pending = set()
            for row in parents:
                if row.id not in included:
                    included[row.id] = row
                    if row.parent_id:
                        pending.add(row.parent_id)

        rows = list(included.values())
        rows.sort(key=lambda r: (r.org_level, r.org_name))
        return rows
