from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import models
from Config import Config
from common.back_to_home_page import back_to_admin_home_page_handler
from common.keyboards import (
    build_admin_keyboard,
    build_back_button,
    build_back_to_home_page_button,
    build_keyboard,
)
from common.lang_dicts import BUTTONS, TEXTS, get_lang
from custom_filters import PermissionFilter, PrivateChatAndAdmin
from models import GeneralSettings, Permission
from start import admin_command

from admin.meta_upload.keyboards import (
    build_caption_keyboard,
    build_media_keyboard,
    build_platform_keyboard,
    build_post_type_keyboard,
    build_preview_keyboard,
    build_when_keyboard,
)


(
    CHOOSE_PAGE,
    CHOOSE_POST_TYPE,
    GET_MEDIA,
    GET_CAPTION,
    CHOOSE_PLATFORM,
    GET_IMAGE_URL,
    WHEN_CHOOSE,
    ENTER_DATETIME,
    PREVIEW,
) = range(9)


def _get_media_from_message(msg):
    if msg.photo:
        # highest resolution
        return "photo", msg.photo[-1].file_id
    if msg.video:
        return "video", msg.video.file_id
    return None, None


def _meta_upload_is_text_only(meta: dict) -> bool:
    return not meta.get("media_type") or not meta.get("media_file_id")


_POST_TYPE_TO_BUTTON_KEY = {
    "reel": "post_type_reel",
    "story": "post_type_story",
    "feed": "post_type_feed",
    "regular": "post_type_feed",
}

_PLATFORM_TO_BUTTON_KEY = {
    "instagram": "platform_instagram",
    "facebook": "platform_facebook",
}


def _format_preview_post_type(lang, post_type: str | None) -> str:
    if not post_type:
        return "—"
    key = _POST_TYPE_TO_BUTTON_KEY.get(post_type)
    if key:
        return BUTTONS[lang][key]
    return post_type


def _format_preview_platforms(lang, platforms: list[str] | str | None) -> str:
    if not platforms:
        return "—"
    if isinstance(platforms, str):
        platforms = ["instagram", "facebook"] if platforms == "both" else [platforms]
    if set(platforms) == {"instagram", "facebook"}:
        return BUTTONS[lang]["platform_both"]
    sep = "، " if lang == models.Language.ARABIC else ", "
    parts: list[str] = []
    for p in platforms:
        key = _PLATFORM_TO_BUTTON_KEY.get(p)
        parts.append(BUTTONS[lang][key] if key else p)
    return sep.join(parts)


def _format_preview_media(lang, media_type: str | None) -> str:
    if not media_type:
        return TEXTS[lang]["meta_upload_preview_media_text"]
    if media_type == "photo":
        return TEXTS[lang]["meta_upload_preview_media_photo"]
    if media_type == "video":
        return TEXTS[lang]["meta_upload_preview_media_video"]
    return media_type


def _build_preview_text(lang, data: dict):
    page_name = data["page_name"]
    post_type_label = _format_preview_post_type(lang, data.get("post_type"))
    platforms_label = _format_preview_platforms(lang, data.get("platforms"))
    media_label = _format_preview_media(lang, data.get("media_type"))
    caption = data.get("caption")
    when_label = data["when_label"]

    caption_text = caption if caption else TEXTS[lang]["meta_upload_preview_no_caption"]

    return (
        f"<b>{TEXTS[lang]['meta_upload_preview_title']}</b>\n\n"
        f"{TEXTS[lang]['meta_upload_preview_page']}: {page_name}\n"
        f"{TEXTS[lang]['meta_upload_preview_post_type']}: {post_type_label}\n"
        f"{TEXTS[lang]['meta_upload_preview_platforms']}: {platforms_label}\n"
        f"{TEXTS[lang]['meta_upload_preview_media']}: {media_label}\n"
        f"{TEXTS[lang]['meta_upload_preview_caption']}: {caption_text}\n"
        f"{TEXTS[lang]['meta_upload_preview_when']}: {when_label}\n\n"
        f"{TEXTS[lang]['meta_upload_preview_confirmation_hint']}"
    )


async def meta_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END

    lang = get_lang(update.effective_user.id)

    if not update.callback_query.data.startswith("back"):
        # clear previous data
        context.user_data.pop("meta_upload", None)
        context.user_data["meta_upload"] = {}

        from meta.errors import format_meta_publish_failure
        from meta.graph_client import list_business_assets  # lazy import

        try:
            assets = await list_business_assets()
        except Exception as e:
            await update.callback_query.edit_message_text(
                text=format_meta_publish_failure(e, lang),
                reply_markup=build_admin_keyboard(lang, update.effective_user.id),
            )
            return ConversationHandler.END
        if not assets:
            await update.callback_query.answer(
                text=TEXTS[lang]["meta_upload_no_assets"],
                show_alert=True,
            )
            return ConversationHandler.END

        context.user_data["meta_upload"]["assets"] = assets
    else:
        assets = context.user_data["meta_upload"]["assets"]

    # One-column keyboard with each page as a separate button.
    keyboard = build_keyboard(
        columns=1,
        texts=[a.get("label") or a["page_name"] for a in assets],
        buttons_data=[f"select_page_{a['page_id']}" for a in assets],
    )
    keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])

    await update.callback_query.edit_message_text(
        text=TEXTS[lang]["meta_upload_choose_page"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSE_PAGE


async def select_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END

    lang = get_lang(update.effective_user.id)
    if not update.callback_query.data.startswith("back"):
        page_id = update.callback_query.data.replace("select_page_", "")
    else:
        page_id = context.user_data["meta_upload"].get("page_id")

    meta_data = context.user_data.get("meta_upload", {})
    assets = meta_data.get("assets", [])
    asset = next((a for a in assets if str(a["page_id"]) == str(page_id)), None)
    if not asset:
        await update.callback_query.answer(
            text=TEXTS[lang]["meta_upload_page_not_found"], show_alert=True
        )
        return CHOOSE_PAGE

    context.user_data["meta_upload"]["page_id"] = asset["page_id"]
    context.user_data["meta_upload"]["page_name"] = (
        asset.get("label") or asset["page_name"]
    )
    context.user_data["meta_upload"]["page_access_token"] = asset.get(
        "page_access_token"
    )
    context.user_data["meta_upload"]["instagram_user_id"] = asset["instagram_user_id"]
    context.user_data["meta_upload"]["instagram_user_name"] = asset[
        "instagram_user_name"
    ]

    keyboard = build_post_type_keyboard(lang)
    keyboard.append(build_back_button("back_to_select_page", lang=lang))
    keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
    await update.callback_query.edit_message_text(
        text=TEXTS[lang]["meta_upload_choose_post_type"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSE_POST_TYPE


back_to_select_page = meta_upload_start


async def select_post_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)
    callback_data = update.callback_query.data
    if not update.callback_query.data.startswith("back"):
        context.user_data["meta_upload"]["post_type"] = callback_data.replace(
            "post_type_", ""
        )

    post_type = context.user_data["meta_upload"].get("post_type")
    keyboard = build_media_keyboard(lang, post_type=post_type)
    keyboard.append(build_back_button("back_to_select_post_type", lang=lang))
    keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
    await update.callback_query.edit_message_text(
        text=TEXTS[lang]["meta_upload_send_media"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return GET_MEDIA


back_to_select_post_type = select_page


async def get_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)

    media_type, file_id = _get_media_from_message(update.message)
    if not media_type:
        await update.message.reply_text(
            text=TEXTS[lang]["meta_upload_invalid_media"],
            reply_markup=ReplyKeyboardRemove(),
        )
        return GET_MEDIA

    # Reels always require video.
    post_type = context.user_data["meta_upload"].get("post_type")
    if post_type == "reel" and media_type != "video":
        await update.message.reply_text(
            text=TEXTS[lang]["meta_err_reel_requires_video"],
            reply_markup=ReplyKeyboardRemove(),
        )
        return GET_MEDIA

    context.user_data["meta_upload"]["media_type"] = media_type
    context.user_data["meta_upload"]["media_file_id"] = file_id
    context.user_data["meta_upload"]["source_chat_id"] = update.effective_chat.id
    context.user_data["meta_upload"]["source_message_id"] = update.message.message_id

    keyboard = build_caption_keyboard(lang)
    keyboard.append(build_back_button("back_to_get_media", lang=lang))
    keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
    await update.message.reply_text(
        text=TEXTS[lang]["meta_upload_enter_caption_optional"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return GET_CAPTION


async def skip_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)
    if not update.callback_query.data.startswith("back"):
        post_type = context.user_data["meta_upload"].get("post_type")
        if post_type == "reel":
            await update.callback_query.answer(
                text=TEXTS[lang]["meta_err_reel_requires_video"],
                show_alert=True,
            )
            return GET_MEDIA
        if post_type == "story":
            await update.callback_query.answer(
                text=TEXTS[lang]["meta_err_story_requires_media"],
                show_alert=True,
            )
            return GET_MEDIA
        context.user_data["meta_upload"]["media_type"] = None
        context.user_data["meta_upload"]["media_file_id"] = None
    back_buttons = [
        build_back_button("back_to_get_media", lang=lang),
        build_back_to_home_page_button(lang=lang, is_admin=True)[0],
    ]
    await update.callback_query.edit_message_text(
        text=TEXTS[lang]["meta_upload_enter_caption_required"],
        reply_markup=InlineKeyboardMarkup(back_buttons),
    )
    return GET_CAPTION


back_to_get_media = select_post_type


async def get_caption_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)

    if update.message:
        media_type = context.user_data["meta_upload"].get("media_type")
        media_file_id = context.user_data["meta_upload"].get("media_file_id")
        if not media_type or not media_file_id:
            back_data = "back_to_get_required_caption"
        else:
            back_data = "back_to_get_optional_caption"
        context.user_data["meta_upload"]["caption"] = update.message.text.strip()

    mu = context.user_data["meta_upload"]
    text_only = _meta_upload_is_text_only(mu)
    platform_prompt = (
        TEXTS[lang]["meta_upload_choose_platform_text_only"]
        if text_only
        else TEXTS[lang]["meta_upload_choose_platform"]
    )
    keyboard = build_platform_keyboard(lang, text_only=text_only)
    keyboard.append(build_back_button(back_data, lang=lang))
    keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
    if update.message:
        await update.message.reply_text(
            text=platform_prompt,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.callback_query.edit_message_text(
            text=platform_prompt,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return CHOOSE_PLATFORM


async def skip_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)

    if not update.callback_query.data.startswith("back"):
        media_type = context.user_data["meta_upload"].get("media_type")
        media_file_id = context.user_data["meta_upload"].get("media_file_id")
        if not media_type or not media_file_id:
            await update.callback_query.answer(
                text=TEXTS[lang]["meta_upload_caption_required_if_no_media"],
                show_alert=True,
            )
            return GET_CAPTION
        context.user_data["meta_upload"]["caption"] = ""

    keyboard = build_platform_keyboard(lang, text_only=False)
    keyboard.append(build_back_button("back_to_get_optional_caption", lang=lang))
    keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
    await update.callback_query.edit_message_text(
        text=TEXTS[lang]["meta_upload_choose_platform"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSE_PLATFORM


back_to_get_required_caption = skip_media
back_to_get_optional_caption = get_media


async def choose_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)

    if not update.callback_query.data.startswith("back"):
        chosen = update.callback_query.data.replace("platform_", "")
        context.user_data["meta_upload"]["platforms"] = chosen
    else:
        chosen = context.user_data["meta_upload"].get("platforms")

    if chosen == "both":
        platforms = ["instagram", "facebook"]
    else:
        platforms = [chosen]

    context.user_data["meta_upload"]["platforms"] = platforms

    post_type = context.user_data["meta_upload"].get("post_type")
    text_only = _meta_upload_is_text_only(context.user_data["meta_upload"])
    if text_only and post_type in ("story", "reel"):
        err_key = (
            "meta_err_reel_requires_video"
            if post_type == "reel"
            else "meta_err_story_requires_media"
        )
        await update.callback_query.answer(
            text=TEXTS[lang][err_key],
            show_alert=True,
        )
        return GET_MEDIA
    if text_only and "instagram" in platforms:
        await update.callback_query.answer(
            text=TEXTS[lang]["meta_upload_text_only_no_instagram"],
            show_alert=True,
        )
        return CHOOSE_PLATFORM

    # Validate Instagram availability if requested
    if "instagram" in platforms and not context.user_data["meta_upload"].get(
        "instagram_user_id"
    ):
        await update.callback_query.answer(
            text=TEXTS[lang]["meta_upload_instagram_not_connected"],
            show_alert=True,
        )
        return CHOOSE_PLATFORM

    back_buttons = [
        build_back_button("back_to_choose_platform", lang=lang),
        build_back_to_home_page_button(lang=lang, is_admin=True)[0],
    ]

    # Instagram photo publishing requires a public `image_url`.
    # If Supabase Storage is configured, we upload the Telegram photo automatically and skip the manual step.
    if (
        "instagram" in platforms
        and context.user_data["meta_upload"].get("media_type") == "photo"
    ):
        supabase_configured = all(
            [
                Config.SUPABASE_URL,
                Config.SUPABASE_SERVICE_ROLE_KEY,
                Config.SUPABASE_STORAGE_BUCKET,
            ]
        )
        if not supabase_configured:
            await update.callback_query.edit_message_text(
                text=TEXTS[lang]["meta_upload_enter_instagram_image_url"],
                reply_markup=InlineKeyboardMarkup(back_buttons),
            )
            return GET_IMAGE_URL

    keyboard = build_when_keyboard(lang)
    keyboard.extend(back_buttons)
    await update.callback_query.edit_message_text(
        text=TEXTS[lang]["meta_upload_choose_when"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WHEN_CHOOSE


back_to_choose_platform = skip_caption


async def get_instagram_image_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)
    if update.message:
        raw = update.message.text.strip()
        if not raw.startswith("http"):
            await update.message.reply_text(
                text=TEXTS[lang]["meta_upload_invalid_image_url"],
            )
            return GET_IMAGE_URL
        context.user_data["meta_upload"]["instagram_image_url"] = raw

    keyboard = build_when_keyboard(lang)
    keyboard.append(build_back_button("back_to_get_instagram_image_url", lang=lang))
    keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
    if update.message:
        await update.message.reply_text(
            text=TEXTS[lang]["meta_upload_choose_when"],
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["meta_upload_choose_when"],
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return WHEN_CHOOSE


back_to_get_instagram_image_url = choose_platform


async def choose_when(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)
    chosen = update.callback_query.data.replace("when_", "")

    if not update.callback_query.data.startswith("back"):
        context.user_data["meta_upload"]["schedule_mode"] = chosen  # now|schedule

    if context.user_data["meta_upload"].get("instagram_image_url"):
        back_data = "back_to_choose_when_image_url"
    else:
        back_data = "back_to_choose_when"
    back_buttons = [
        build_back_button(back_data, lang=lang),
        build_back_to_home_page_button(lang=lang, is_admin=True)[0],
    ]
    if chosen == "now":
        keyboard = build_preview_keyboard(lang)
        keyboard.extend(back_buttons)
        await update.callback_query.edit_message_text(
            text=_build_preview_text(
                lang,
                {
                    "page_name": context.user_data["meta_upload"].get("page_name"),
                    "post_type": context.user_data["meta_upload"].get("post_type"),
                    "platforms": context.user_data["meta_upload"].get("platforms"),
                    "media_type": context.user_data["meta_upload"].get("media_type"),
                    "caption": context.user_data["meta_upload"].get("caption"),
                    "when_label": TEXTS[lang]["meta_upload_when_now_label"],
                },
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return PREVIEW

    # schedule
    await update.callback_query.edit_message_text(
        text=TEXTS[lang]["meta_upload_enter_datetime_future"],
        reply_markup=InlineKeyboardMarkup(back_buttons),
    )
    return ENTER_DATETIME


back_to_choose_when = choose_platform
back_to_choose_when_image_url = get_instagram_image_url


async def enter_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)

    if update.message:
        raw = update.message.text.strip()
        context.user_data["meta_upload"]["scheduled_utc_raw"] = raw
    else:
        raw = context.user_data["meta_upload"].get("scheduled_utc_raw")
    try:
        local_dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text(
            text=TEXTS[lang]["meta_upload_invalid_datetime_format"]
        )
        return ENTER_DATETIME

    with models.session_scope() as s:
        settings = s.query(GeneralSettings).first()
        offset_hours = settings.meta_timezone_offset_hours if settings else 3

    scheduled_utc = local_dt.replace(tzinfo=timezone.utc) - timedelta(
        hours=offset_hours
    )
    context.user_data["meta_upload"]["scheduled_utc"] = scheduled_utc.isoformat()
    context.user_data["meta_upload"]["schedule_mode"] = "schedule"

    text = _build_preview_text(
        lang,
        {
            "page_name": context.user_data["meta_upload"].get("page_name"),
            "post_type": context.user_data["meta_upload"].get("post_type"),
            "platforms": context.user_data["meta_upload"].get("platforms"),
            "media_type": context.user_data["meta_upload"].get("media_type"),
            "caption": context.user_data["meta_upload"].get("caption"),
            "when_label": raw,
        },
    )
    keyboard = build_preview_keyboard(lang)
    keyboard.append(build_back_button("back_to_enter_datetime", lang=lang))
    keyboard.append(build_back_to_home_page_button(lang=lang, is_admin=True)[0])
    if update.message:
        await update.message.reply_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return PREVIEW


back_to_enter_datetime = choose_when


async def confirm_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        PrivateChatAndAdmin().filter(update)
        and PermissionFilter(Permission.UPLOAD_TO_META).filter(update)
    ):
        return ConversationHandler.END
    lang = get_lang(update.effective_user.id)

    # Prepare payload
    payload = copy.deepcopy(context.user_data["meta_upload"])
    payload["admin_id"] = update.effective_user.id
    payload["lang"] = lang
    # Do not pass every page token (full assets list) into jobs / long-lived copies.
    payload.pop("assets", None)

    # Convert isoformat for scheduled
    if payload.get("scheduled_utc"):
        payload["scheduled_utc_dt"] = datetime.fromisoformat(
            payload["scheduled_utc"]
        ).astimezone(timezone.utc)
    else:
        payload["scheduled_utc_dt"] = None

    # Create DB record for tracking request lifecycle.
    meta_post_id = None
    platforms = payload.get("platforms") or []
    platforms_str = (
        ",".join(platforms) if isinstance(platforms, list) else str(platforms)
    )

    schedule_mode = payload.get("schedule_mode") or "now"
    status = "scheduled" if schedule_mode == "schedule" else "created"

    with models.session_scope() as s:
        meta_post = models.MetaPost(
            admin_id=update.effective_user.id,
            page_id=payload["page_id"],
            page_name=payload["page_name"],
            instagram_user_id=payload["instagram_user_id"],
            instagram_user_name=payload["instagram_user_name"],
            post_type=payload["post_type"],
            media_type=payload.get("media_type"),
            media_file_id=payload.get("media_file_id"),
            instagram_image_url=payload.get("instagram_image_url"),
            caption=payload.get("caption"),
            platforms=platforms_str,
            schedule_mode=schedule_mode,
            scheduled_utc_iso=(
                payload.get("scheduled_utc") if schedule_mode == "schedule" else None
            ),
            scheduled_local_text=(
                payload.get("scheduled_utc_raw")
                if schedule_mode == "schedule"
                else None
            ),
            status=status,
        )
        s.add(meta_post)
        s.flush()
        meta_post_id = meta_post.id

    # Lazy import to avoid startup errors during partial dev
    from meta.publishers import publish_to_meta  # noqa: F401
    from meta.publish_notifications import send_publish_report  # noqa: F401

    if schedule_mode == "now":
        await update.callback_query.edit_message_text(
            text=TEXTS[lang]["meta_upload_publishing_now"],
        )
        try:
            result = await publish_to_meta(payload=payload, context=context)
            with models.session_scope() as s:
                meta_post = s.get(models.MetaPost, meta_post_id)
                if meta_post:
                    meta_post.status = "published"
                    meta_post.meta_response = result
                    meta_post.last_error = None

            await send_publish_report(
                context,
                status="published",
                meta_post_id=meta_post_id,
                payload=payload,
                meta_response=result,
            )

            await update.callback_query.edit_message_text(
                text=result,
                reply_markup=build_admin_keyboard(
                    lang, user_id=update.effective_user.id
                ),
            )
        except Exception as e:
            from meta.errors import format_meta_publish_failure

            failure_text = format_meta_publish_failure(e, lang)

            with models.session_scope() as s:
                meta_post = s.get(models.MetaPost, meta_post_id)
                if meta_post:
                    meta_post.status = "failed"
                    meta_post.meta_response = None
                    meta_post.last_error = str(e)

            await send_publish_report(
                context,
                status="failed",
                meta_post_id=meta_post_id,
                payload=payload,
                last_error=f"{failure_text}\n\nraw_error: {str(e)}",
            )

            await update.callback_query.edit_message_text(
                text=failure_text,
                reply_markup=build_admin_keyboard(
                    lang, user_id=update.effective_user.id
                ),
            )
        return ConversationHandler.END

    from jobs import schedule_publish_to_meta

    scheduled_dt = payload["scheduled_utc_dt"]
    context.job_queue.run_once(
        callback=schedule_publish_to_meta,
        name=f"schedule_publish_to_meta",
        when=scheduled_dt,
        data={
            "payload": payload,
            "lang": lang,
            "meta_post_id": meta_post_id,
        },
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        job_kwargs={
            "misfire_grace_time": None,
        },
    )
    # Log scheduling so we can correlate execution logs with the user request.
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        "Scheduled publish queued: meta_post_id=%s user_id=%s page_id=%s post_type=%s platforms=%s when=%s",
        meta_post_id,
        update.effective_user.id,
        payload.get("page_id"),
        payload.get("post_type"),
        payload.get("platforms"),
        scheduled_dt.isoformat() if scheduled_dt else None,
    )
    await update.callback_query.edit_message_text(
        text=TEXTS[lang]["meta_upload_scheduled_success"].format(
            time=context.user_data["meta_upload"].get("scheduled_utc")
        ),
        reply_markup=build_admin_keyboard(lang, user_id=update.effective_user.id),
    )
    return ConversationHandler.END


meta_upload_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(
            callback=meta_upload_start,
            pattern="^meta_upload$|^back_to_meta_upload$",
        )
    ],
    states={
        CHOOSE_PAGE: [
            CallbackQueryHandler(
                callback=select_page,
                pattern="^select_page_",
            )
        ],
        CHOOSE_POST_TYPE: [
            CallbackQueryHandler(
                callback=select_post_type,
                pattern="^post_type_",
            )
        ],
        GET_MEDIA: [
            MessageHandler(
                filters=(filters.PHOTO | filters.VIDEO) & ~filters.COMMAND,
                callback=get_media,
            ),
            CallbackQueryHandler(
                callback=skip_media,
                pattern="^skip_media$",
            ),
        ],
        GET_CAPTION: [
            MessageHandler(
                filters=(filters.TEXT & ~filters.COMMAND) | filters.Caption,
                callback=get_caption_text,
            ),
            CallbackQueryHandler(
                callback=skip_caption,
                pattern="^skip_caption$",
            ),
        ],
        CHOOSE_PLATFORM: [
            CallbackQueryHandler(
                callback=choose_platform,
                pattern="^platform_",
            )
        ],
        GET_IMAGE_URL: [
            MessageHandler(
                filters=(filters.TEXT & ~filters.COMMAND),
                callback=get_instagram_image_url,
            )
        ],
        WHEN_CHOOSE: [
            CallbackQueryHandler(
                callback=choose_when,
                pattern="^when_",
            )
        ],
        ENTER_DATETIME: [
            MessageHandler(
                filters=(filters.TEXT & ~filters.COMMAND),
                callback=enter_datetime,
            )
        ],
        PREVIEW: [
            CallbackQueryHandler(
                callback=confirm_publish,
                pattern="^confirm_publish$",
            ),
        ],
    },
    fallbacks=[
        admin_command,
        back_to_admin_home_page_handler,
        CallbackQueryHandler(back_to_select_page, r"^back_to_select_page$"),
        CallbackQueryHandler(back_to_select_post_type, r"^back_to_select_post_type$"),
        CallbackQueryHandler(back_to_get_media, r"^back_to_get_media$"),
        CallbackQueryHandler(
            back_to_get_required_caption, r"^back_to_get_required_caption$"
        ),
        CallbackQueryHandler(
            back_to_get_optional_caption, r"^back_to_get_optional_caption$"
        ),
        CallbackQueryHandler(back_to_choose_platform, r"^back_to_choose_platform$"),
        CallbackQueryHandler(
            back_to_get_instagram_image_url, r"^back_to_get_instagram_image_url$"
        ),
        CallbackQueryHandler(back_to_choose_when, r"^back_to_choose_when$"),
        CallbackQueryHandler(
            back_to_choose_when_image_url, r"^back_to_choose_when_image_url$"
        ),
        CallbackQueryHandler(back_to_enter_datetime, r"^back_to_enter_datetime$"),
    ],
    name="meta_upload_handler",
    persistent=True,
)
