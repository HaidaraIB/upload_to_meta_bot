from datetime import datetime
import sqlalchemy as sa
from models.DB import Base


class DriveFolder(Base):
    __tablename__ = "drive_folders"

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    name = sa.Column(sa.String, nullable=False)
    folder_id = sa.Column(sa.String, nullable=False, unique=True)

    page_id = sa.Column(sa.String, nullable=True)
    page_name = sa.Column(sa.String, nullable=True)
    instagram_user_id = sa.Column(sa.String, nullable=True)
    instagram_user_name = sa.Column(sa.String, nullable=True)

    is_active = sa.Column(sa.Boolean, nullable=False, default=True)

    created_at = sa.Column(sa.DateTime, default=datetime.now)
    updated_at = sa.Column(sa.DateTime, default=datetime.now, onupdate=datetime.now)

    def __str__(self):
        linked = "linked" if self.page_id else "not linked"
        return (
            f"ID: <code>{self.id}</code>\n"
            f"Name: <b>{self.name}</b>\n"
            f"Folder ID: <code>{self.folder_id}</code>\n"
            f"Meta Link: {linked}"
        )
