from telegram import InlineKeyboardButton

import models
from common.lang_dicts import BUTTONS


def build_google_drive_settings_keyboard(
    lang: models.Language = models.Language.ARABIC,
):
    return [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["add_drive_folder"],
                callback_data="add_drive_folder",
            ),
            InlineKeyboardButton(
                text=BUTTONS[lang]["remove_drive_folder"],
                callback_data="remove_drive_folder",
            ),
        ],
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["show_drive_folders"],
                callback_data="show_drive_folders",
            )
        ],
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["link_drive_folder"],
                callback_data="link_drive_folder",
            ),
            InlineKeyboardButton(
                text=BUTTONS[lang]["unlink_drive_folder"],
                callback_data="unlink_drive_folder",
            ),
        ],
    ]

