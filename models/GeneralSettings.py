import sqlalchemy as sa
from models.DB import Base
from datetime import datetime


class GeneralSettings(Base):
    """
    Generic single-row settings table.
    """

    __tablename__ = "general_settings"

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    # Example: 3 means UTC+3
    meta_timezone_offset_hours = sa.Column(sa.Integer, default=3)

    created_at = sa.Column(sa.DateTime, default=datetime.now)
    updated_at = sa.Column(sa.DateTime, default=datetime.now, onupdate=datetime.now)

    @staticmethod
    def get_or_create(session) -> "GeneralSettings":
        row = session.query(GeneralSettings).first()
        if row:
            return row
        row = GeneralSettings(meta_timezone_offset_hours=3)
        session.add(row)
        session.commit()
        return row

