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
                text=BUTTONS[lang]["post_type_regular"],
                callback_data="post_type_regular",
            )
        ],
    ]


def build_platform_keyboard(lang: models.Language):
    return [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["platform_instagram"],
                callback_data="platform_instagram",
            ),
            InlineKeyboardButton(
                text=BUTTONS[lang]["platform_facebook"],
                callback_data="platform_facebook",
            ),
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


def build_media_keyboard(lang: models.Language):
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
            InlineKeyboardButton(
                text=BUTTONS[lang]["cancel_publish"],
                callback_data="cancel_publish",
            ),
        ]
    ]
