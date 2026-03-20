from __future__ import annotations

import sqlalchemy as sa
from datetime import datetime

from models.DB import Base


class MetaPost(Base):
    """
    Stores admin-created Meta posting requests + publishing result.
    """

    __tablename__ = "meta_posts"

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)

    # Who requested the post
    admin_id = sa.Column(
        sa.BigInteger,
        sa.ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Meta business asset (Facebook Page)
    page_id = sa.Column(sa.BigInteger, nullable=True)
    page_name = sa.Column(sa.String, nullable=True)

    # Instagram user (if applicable)
    instagram_user_id = sa.Column(sa.String, nullable=True)
    instagram_user_name = sa.Column(sa.String, nullable=True)

    # Step 2/4/5 fields
    post_type = sa.Column(sa.String, nullable=False)  # reel|story|feed (feed post)
    media_type = sa.Column(sa.String, nullable=True)  # photo|video|None
    media_file_id = sa.Column(sa.String, nullable=True)  # Telegram file_id (source)
    instagram_image_url = sa.Column(sa.Text, nullable=True)  # required for IG photo
    caption = sa.Column(sa.Text, nullable=True)  # caption/text
    platforms = sa.Column(sa.Text, nullable=False)  # "instagram,facebook" (comma-separated)

    # Scheduling (Step 6)
    schedule_mode = sa.Column(sa.String, nullable=False)  # now|schedule
    scheduled_utc_iso = sa.Column(sa.String, nullable=True)  # ISO string in UTC
    scheduled_local_text = sa.Column(sa.String, nullable=True)  # user-entered text (for audit)

    # Result
    status = sa.Column(
        sa.String,
        nullable=False,
        default="created",  # created|published|failed|cancelled|scheduled
    )
    meta_response = sa.Column(sa.Text, nullable=True)
    last_error = sa.Column(sa.Text, nullable=True)


    created_at = sa.Column(sa.DateTime, default=datetime.now)
    updated_at = sa.Column(sa.DateTime, default=datetime.now, onupdate=datetime.now)

