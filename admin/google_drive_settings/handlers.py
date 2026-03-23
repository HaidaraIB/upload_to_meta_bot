from telegram import InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import models
from admin.google_drive_settings.keyboards import build_google_drive_settings_keyboard
from common.back_to_home_page import back_to_admin_home_page_handler
from common.keyboards import (
    build_admin_keyboard,
    build_back_button,
    build_back_to_home_page_button,
    build_keyboard,
)
from common.lang_dicts import TEXTS, get_lang
from custom_filters import PermissionFilter, PrivateChatAndAdmin
from models import Permission
from start import admin_command

(
    ADD_FOLDER_NAME,
    ADD_FOLDER_ID,
    CHOOSE_FOLDER_TO_REMOVE,
    CHOOSE_FOLDER_TO_LINK,
    CHOOSE_ASSET_TO_LINK,
    CHOOSE_FOLDER_TO_UNLINK,
) = range(6)


async def google_drive_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        keyboard = build_google_drive_settings_keyboard(lang)
        keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["google_drive_settings_title"],
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ConversationHandler.END


google_drive_settings_handler = CallbackQueryHandler(
    callback=google_drive_settings,
    pattern="^google_drive_settings$|^back_to_google_drive_settings$",
)


async def add_drive_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        back_buttons = [
            build_back_button(data="back_to_google_drive_settings", lang=lang),
            build_back_to_home_page_button(lang=lang, is_admin=True)[0],
        ]
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["drive_folder_add_name_instruction"],
            reply_markup=InlineKeyboardMarkup(back_buttons),
        )
        return ADD_FOLDER_NAME


async def save_folder_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        back_buttons = [
            build_back_button(data="back_to_save_folder_name", lang=lang),
            build_back_to_home_page_button(lang=lang, is_admin=True)[0],
        ]
        if update.message:
            context.user_data["drive_folder_name"] = update.message.text.strip()
            await update.message.reply_text(
                text=TEXTS[lang]["drive_folder_add_id_instruction"],
                reply_markup=InlineKeyboardMarkup(back_buttons),
            )
        else:
            await update.callback_query.edit_message_text(
                text=TEXTS[lang]["drive_folder_add_id_instruction"],
                reply_markup=InlineKeyboardMarkup(back_buttons),
            )

        return ADD_FOLDER_ID


back_to_save_folder_name = add_drive_folder


async def save_folder_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        folder_name = context.user_data.get("drive_folder_name", "").strip()
        folder_id = update.message.text.strip()

        with models.session_scope() as s:
            existing = (
                s.query(models.DriveFolder)
                .filter(models.DriveFolder.folder_id == folder_id)
                .first()
            )
            if existing:
                existing.name = folder_name
                existing.is_active = True
            else:
                s.add(models.DriveFolder(name=folder_name, folder_id=folder_id))

        context.user_data.pop("drive_folder_name", None)
        await update.message.reply_text(
            text=TEXTS[lang]["drive_folder_added_success"],
            reply_markup=build_admin_keyboard(
                lang=lang, user_id=update.effective_user.id
            ),
        )
        return ConversationHandler.END


add_drive_folder_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(
            callback=add_drive_folder,
            pattern=r"^add_drive_folder$",
        ),
    ],
    states={
        ADD_FOLDER_NAME: [
            MessageHandler(
                filters=(filters.TEXT & ~filters.COMMAND),
                callback=save_folder_name,
            )
        ],
        ADD_FOLDER_ID: [
            MessageHandler(
                filters=(filters.TEXT & ~filters.COMMAND),
                callback=save_folder_id,
            )
        ],
    },
    fallbacks=[
        google_drive_settings_handler,
        admin_command,
        back_to_admin_home_page_handler,
        CallbackQueryHandler(
            callback=back_to_save_folder_name, pattern=r"^back_to_save_folder_name$"
        ),
    ],
)


async def remove_drive_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        with models.session_scope() as s:
            if update.callback_query.data.startswith("remove_drive_folder_"):
                row_id = int(
                    update.callback_query.data.replace("remove_drive_folder_", "")
                )
                row = s.get(models.DriveFolder, row_id)
                if row:
                    s.delete(row)
                    await update.callback_query.answer(
                        text=TEXTS[lang]["drive_folder_removed_success"],
                        show_alert=True,
                    )

            rows = (
                s.query(models.DriveFolder).order_by(models.DriveFolder.id.desc()).all()
            )
            if not rows:
                await update.callback_query.answer(
                    text=TEXTS[lang]["drive_folder_no_items"], show_alert=True
                )
                return ConversationHandler.END

            keyboard = build_keyboard(
                columns=1,
                texts=[f"{r.name} ({r.folder_id})" for r in rows],
                buttons_data=[f"remove_drive_folder_{r.id}" for r in rows],
            )
            keyboard.append(
                build_back_button("back_to_google_drive_settings", lang=lang)
            )
            keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
            await update.callback_query.edit_message_text(
                text=TEXTS[lang]["drive_folder_remove_instruction"],
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return CHOOSE_FOLDER_TO_REMOVE


remove_drive_folder_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(
            callback=remove_drive_folder,
            pattern=r"^remove_drive_folder$",
        ),
    ],
    states={
        CHOOSE_FOLDER_TO_REMOVE: [
            CallbackQueryHandler(
                callback=remove_drive_folder,
                pattern=r"^remove_drive_folder_",
            )
        ]
    },
    fallbacks=[
        google_drive_settings_handler,
        admin_command,
        back_to_admin_home_page_handler,
    ],
)


def _format_folder_line(lang: models.Language, row: models.DriveFolder) -> str:
    if row.page_id:
        linked_to = f"{row.page_name or row.page_id}"
        if row.instagram_user_name:
            linked_to = f"{linked_to} / IG: {row.instagram_user_name}"
        link_line = (
            f"{TEXTS[lang]['drive_folder_details_linked_to']}: <b>{linked_to}</b>"
        )
    else:
        link_line = TEXTS[lang]["drive_folder_details_not_linked"]
    return (
        f"• <b>{row.name}</b>\n"
        f"  Folder ID: <code>{row.folder_id}</code>\n"
        f"  {link_line}"
    )


async def show_drive_folders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        with models.session_scope() as s:
            rows = (
                s.query(models.DriveFolder).order_by(models.DriveFolder.id.desc()).all()
            )

        if not rows:
            await update.callback_query.answer(
                text=TEXTS[lang]["drive_folder_no_items"], show_alert=True
            )
            return

        text = TEXTS[lang]["drive_folder_list_title"] + "\n\n"
        text += "\n\n".join(_format_folder_line(lang, row) for row in rows)

        keyboard = [
            build_back_button("back_to_google_drive_settings", lang=lang),
            build_back_to_home_page_button(lang=lang, is_admin=True)[0],
        ]
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


show_drive_folders_handler = CallbackQueryHandler(
    callback=show_drive_folders,
    pattern=r"^show_drive_folders$",
)


async def link_drive_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        with models.session_scope() as s:
            rows = (
                s.query(models.DriveFolder).order_by(models.DriveFolder.id.desc()).all()
            )
        if not rows:
            await update.callback_query.answer(
                text=TEXTS[lang]["drive_folder_no_items"], show_alert=True
            )
            return ConversationHandler.END

        keyboard = build_keyboard(
            columns=1,
            texts=[f"{r.name} ({r.folder_id})" for r in rows],
            buttons_data=[f"choose_link_drive_folder_{r.id}" for r in rows],
        )
        keyboard.append(build_back_button("back_to_google_drive_settings", lang=lang))
        keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["drive_folder_link_choose_folder"],
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return CHOOSE_FOLDER_TO_LINK


async def choose_folder_to_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        folder_row_id = int(
            update.callback_query.data.replace("choose_link_drive_folder_", "")
        )
        context.user_data["selected_drive_folder_id"] = folder_row_id

        from meta.graph_client import list_business_assets  # lazy import

        assets = await list_business_assets()
        if not assets:
            await update.callback_query.answer(
                text=TEXTS[lang]["meta_upload_no_assets"], show_alert=True
            )
            return ConversationHandler.END

        context.user_data["drive_assets"] = assets
        keyboard = build_keyboard(
            columns=1,
            texts=[a.get("label") or a["page_name"] for a in assets],
            buttons_data=[f"link_asset_{a['page_id']}" for a in assets],
        )
        keyboard.append(
            build_back_button("back_to_link_drive_folder_choose_folder", lang=lang)
        )
        keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["drive_folder_link_choose_asset"],
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return CHOOSE_ASSET_TO_LINK


back_to_link_drive_folder_choose_folder = link_drive_folder


async def save_folder_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        page_id = update.callback_query.data.replace("link_asset_", "")
        folder_row_id = context.user_data.get("selected_drive_folder_id")
        assets = context.user_data.get("drive_assets", [])
        asset = next((a for a in assets if str(a["page_id"]) == str(page_id)), None)
        if not asset:
            await update.callback_query.answer(
                text=TEXTS[lang]["drive_folder_asset_not_found"], show_alert=True
            )
            return CHOOSE_ASSET_TO_LINK

        with models.session_scope() as s:
            folder = s.get(models.DriveFolder, folder_row_id)
            if not folder:
                await update.callback_query.answer(
                    text=TEXTS[lang]["drive_folder_no_items"], show_alert=True
                )
                return ConversationHandler.END
            folder.page_id = str(asset["page_id"])
            folder.page_name = asset.get("page_name")
            folder.instagram_user_id = asset.get("instagram_user_id")
            folder.instagram_user_name = asset.get("instagram_user_name")

        context.user_data.pop("selected_drive_folder_id", None)
        context.user_data.pop("drive_assets", None)
        await update.callback_query.answer(
            text=TEXTS[lang]["drive_folder_link_saved_success"], show_alert=True
        )
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["home_page"],
            reply_markup=build_admin_keyboard(
                lang=lang, user_id=update.effective_user.id
            ),
        )
        return ConversationHandler.END


link_drive_folder_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(
            callback=link_drive_folder,
            pattern=r"^link_drive_folder$",
        ),
    ],
    states={
        CHOOSE_FOLDER_TO_LINK: [
            CallbackQueryHandler(
                callback=choose_folder_to_link,
                pattern=r"^choose_link_drive_folder_",
            )
        ],
        CHOOSE_ASSET_TO_LINK: [
            CallbackQueryHandler(
                callback=save_folder_link,
                pattern=r"^link_asset_",
            )
        ],
    },
    fallbacks=[
        google_drive_settings_handler,
        admin_command,
        back_to_admin_home_page_handler,
        CallbackQueryHandler(
            callback=back_to_link_drive_folder_choose_folder,
            pattern=r"^back_to_link_drive_folder_choose_folder$",
        ),
    ],
)


async def unlink_drive_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update) and PermissionFilter(
        Permission.MANAGE_GOOGLE_DRIVE_SETTINGS
    ).filter(update):
        lang = get_lang(update.effective_user.id)
        with models.session_scope() as s:
            if update.callback_query.data.startswith("unlink_drive_folder_"):
                row_id = int(
                    update.callback_query.data.replace("unlink_drive_folder_", "")
                )
                row = s.get(models.DriveFolder, row_id)
                if row:
                    row.page_id = None
                    row.page_name = None
                    row.instagram_user_id = None
                    row.instagram_user_name = None
                    await update.callback_query.answer(
                        text=TEXTS[lang]["drive_folder_unlink_saved_success"],
                        show_alert=True,
                    )

            rows = (
                s.query(models.DriveFolder)
                .filter(models.DriveFolder.page_id.isnot(None))
                .order_by(models.DriveFolder.id.desc())
                .all()
            )
            if not rows:
                await update.callback_query.answer(
                    text=TEXTS[lang]["drive_folder_no_items"], show_alert=True
                )
                return ConversationHandler.END

            keyboard = build_keyboard(
                columns=1,
                texts=[f"{r.name} ({r.folder_id})" for r in rows],
                buttons_data=[f"unlink_drive_folder_{r.id}" for r in rows],
            )
            keyboard.append(
                build_back_button("back_to_google_drive_settings", lang=lang)
            )
            keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
            await update.callback_query.edit_message_text(
                text=TEXTS[lang]["drive_folder_unlink_choose_folder"],
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return CHOOSE_FOLDER_TO_UNLINK


unlink_drive_folder_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(
            callback=unlink_drive_folder,
            pattern=r"^unlink_drive_folder$",
        ),
    ],
    states={
        CHOOSE_FOLDER_TO_UNLINK: [
            CallbackQueryHandler(
                callback=unlink_drive_folder,
                pattern=r"^unlink_drive_folder_",
            )
        ]
    },
    fallbacks=[
        google_drive_settings_handler,
        admin_command,
        back_to_admin_home_page_handler,
    ],
)
