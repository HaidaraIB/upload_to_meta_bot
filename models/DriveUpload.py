from datetime import datetime
import sqlalchemy as sa
from models.DB import Base


class DriveUpload(Base):
    """
    Stores Google Drive archive status for each MetaPost.
    """

    __tablename__ = "drive_uploads"

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    meta_post_id = sa.Column(
        sa.Integer,
        sa.ForeignKey("meta_posts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    admin_id = sa.Column(
        sa.BigInteger,
        sa.ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )

    page_id = sa.Column(sa.String, nullable=True)
    page_name = sa.Column(sa.String, nullable=True)
    drive_folder_id = sa.Column(sa.String, nullable=True)
    drive_file_id = sa.Column(sa.String, nullable=True)

    # success|skipped_no_link|skipped_not_video|failed|not_attempted_meta_failed
    status = sa.Column(sa.String, nullable=False, default="skipped_not_video")
    error_detail = sa.Column(sa.Text, nullable=True)

    created_at = sa.Column(sa.DateTime, default=datetime.now)
    updated_at = sa.Column(sa.DateTime, default=datetime.now, onupdate=datetime.now)
