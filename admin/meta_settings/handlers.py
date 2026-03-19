from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from common.keyboards import (
    build_admin_keyboard,
    build_back_button,
    build_back_to_home_page_button,
)
from common.back_to_home_page import back_to_admin_home_page_handler
from common.lang_dicts import TEXTS, get_lang
from custom_filters import PrivateChatAndAdmin, PermissionFilter
import models
from models import Permission, GeneralSettings
from admin.meta_settings.keyboards import build_meta_settings_keyboard
from start import admin_command


META_SETTINGS_HOME, ENTER_OFFSET = range(2)


async def meta_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_META_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        offset = 3
        with models.session_scope() as s:
            settings = s.query(GeneralSettings).first()
            if settings:
                offset = settings.meta_timezone_offset_hours
            else:
                settings = GeneralSettings(meta_timezone_offset_hours=offset)
                s.add(settings)
        keyboard = build_meta_settings_keyboard(lang)
        keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["meta_settings_current_offset"].format(offset=offset),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return META_SETTINGS_HOME


async def change_meta_offset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_META_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        back_buttons = [
            build_back_button("back_to_meta_settings", lang=lang),
            build_back_to_home_page_button(lang=lang, is_admin=True)[0],
        ]
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["meta_settings_enter_offset"],
            reply_markup=InlineKeyboardMarkup(back_buttons),
        )
        return ENTER_OFFSET


back_to_meta_settings = meta_settings


async def save_meta_offset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_META_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        raw = update.message.text.strip()
        offset = int(raw)

        if offset < -12 or offset > 14:
            await update.message.reply_text(
                text=TEXTS[lang]["meta_settings_invalid_offset"],
            )
            return ENTER_OFFSET

        with models.session_scope() as s:
            settings = s.query(GeneralSettings).first()
            if not settings:
                settings = GeneralSettings(meta_timezone_offset_hours=offset)
                s.add(settings)
            else:
                settings.meta_timezone_offset_hours = offset

        await update.message.reply_text(
            text=TEXTS[lang]["meta_settings_saved_success"].format(offset=offset),
            reply_markup=build_admin_keyboard(
                lang=lang, user_id=update.effective_user.id
            ),
        )
        return ConversationHandler.END


meta_settings_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(
            callback=meta_settings,
            pattern="^meta_settings$|^back_to_meta_settings$",
        ),
    ],
    states={
        META_SETTINGS_HOME: [
            CallbackQueryHandler(
                callback=change_meta_offset,
                pattern="^change_meta_offset$",
            ),
        ],
        ENTER_OFFSET: [
            MessageHandler(
                filters=filters.Regex(r"^-?\d+$") & ~filters.COMMAND,
                callback=save_meta_offset,
            ),
        ],
    },
    fallbacks=[
        CallbackQueryHandler(back_to_meta_settings, r"^back_to_meta_settings$"),
        admin_command,
        back_to_admin_home_page_handler,
    ],
)
