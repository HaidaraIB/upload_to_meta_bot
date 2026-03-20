from telegram import InlineKeyboardButton
import models
from common.lang_dicts import BUTTONS


def build_post_type_keyboard(lang: models.Language = models.Language.ARABIC):
    return [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["post_type_reel"],
                callback_data="post_type_reel",
            ),
            InlineKeyboardButton(
                text=BUTTONS[lang]["post_type_story"],
                callback_data="post_type_story",
            ),
        ],
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["post_type_feed"],
                callback_data="post_type_feed",
            )
        ],
    ]


def build_platform_keyboard(
    lang: models.Language, *, text_only: bool = False
):
    """If text_only, only Facebook — Instagram Graph API needs photo/video."""
    fb = InlineKeyboardButton(
        text=BUTTONS[lang]["platform_facebook"],
        callback_data="platform_facebook",
    )
    if text_only:
        return [[fb]]

    return [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["platform_instagram"],
                callback_data="platform_instagram",
            ),
            fb,
        ],
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["platform_both"],
                callback_data="platform_both",
            )
        ],
    ]


def build_when_keyboard(lang: models.Language):
    return [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["when_now"],
                callback_data="when_now",
            ),
            InlineKeyboardButton(
                text=BUTTONS[lang]["when_schedule"],
                callback_data="when_schedule",
            ),
        ]
    ]


def build_media_keyboard(lang: models.Language, *, post_type: str | None = None):
    """
    - feed: يسمح بـ text-only عبر skip_media (Facebook يحتاج caption/text).
    - story/reel: يمنع text-only عبر عدم إظهار زر skip_media.
    """
    if post_type in ("story", "reel"):
        return []
    return [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["skip_media"],
                callback_data="skip_media",
            )
        ]
    ]


def build_caption_keyboard(lang: models.Language = models.Language.ARABIC):
    keyboard = [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["skip_caption"],
                callback_data="skip_caption",
            )
        ]
    ]
    return keyboard


def build_preview_keyboard(lang: models.Language):
    return [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["confirm_publish"],
                callback_data="confirm_publish",
            ),
        ]
    ]
