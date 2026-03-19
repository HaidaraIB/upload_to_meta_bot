from telegram import InlineKeyboardButton
import models
from common.lang_dicts import BUTTONS


def build_meta_settings_keyboard(lang: models.Language):
    keyboard = [
        [
            InlineKeyboardButton(
                text=BUTTONS[lang]["change_meta_offset"],
                callback_data="change_meta_offset",
            )
        ]
    ]
    return keyboard

