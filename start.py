import logging

from telegram import Update, BotCommandScopeChat, Bot
from telegram.ext import CommandHandler, ContextTypes, Application, ConversationHandler
from common.decorators import is_user_banned, add_new_user, is_user_member
from common.keyboards import build_user_keyboard, build_admin_keyboard
from common.common import check_hidden_permission_requests_keyboard
from common.lang_dicts import TEXTS, get_lang
from custom_filters import Admin, PrivateChat, PrivateChatAndAdmin
from Config import Config
import models

logger = logging.getLogger(__name__)


def log_media_pipeline_config() -> None:
    """
    Log the media settings actually in effect on this host.

    Without ffprobe every Instagram codec, dimension and Reels-spec check silently
    no-ops and the file reaches Meta unverified, so the state of these two binaries
    is the first thing to check when reels start failing.
    """
    from meta.video_normalizer import ffmpeg_available, ffprobe_available

    has_ffmpeg = ffmpeg_available()
    has_ffprobe = ffprobe_available()

    logger.info(
        "Media pipeline config: FFMPEG_BIN=%s ffmpeg_available=%s ffprobe_available=%s "
        "META_GRAPH_VERSION=%s IG_VIDEO_FORCE_REENCODE=%s IG_VIDEO_REENCODE_IF_INCOMPATIBLE=%s "
        "IG_VIDEO_AUTOFIX_ENABLED=%s IG_VIDEO_STRICT_PROBE=%s IG_VIDEO_CRF=%s "
        "IG_VIDEO_ENCODE_PRESET=%s IG_VIDEO_AUDIO_BITRATE=%s IG_VIDEO_FFMPEG_TIMEOUT=%s "
        "IG_REELS_TARGET_FPS=%s IG_REELS_SAFE_MODE_RETRY=%s",
        Config.FFMPEG_BIN,
        has_ffmpeg,
        has_ffprobe,
        Config.META_GRAPH_VERSION,
        Config.IG_VIDEO_FORCE_REENCODE,
        Config.IG_VIDEO_REENCODE_IF_INCOMPATIBLE,
        Config.IG_VIDEO_AUTOFIX_ENABLED,
        Config.IG_VIDEO_STRICT_PROBE,
        Config.IG_VIDEO_CRF,
        Config.IG_VIDEO_ENCODE_PRESET,
        Config.IG_VIDEO_AUDIO_BITRATE,
        Config.IG_VIDEO_FFMPEG_TIMEOUT,
        Config.IG_REELS_TARGET_FPS,
        Config.IG_REELS_SAFE_MODE_RETRY,
    )

    if not has_ffmpeg:
        logger.error(
            "ffmpeg is NOT available (FFMPEG_BIN=%s). Instagram videos cannot be "
            "normalized and will be rejected at ingest.",
            Config.FFMPEG_BIN,
        )
    if not has_ffprobe:
        logger.error(
            "ffprobe is NOT available next to FFMPEG_BIN=%s. Codec, dimension and "
            "Reels-spec checks are all skipped; files reach Meta unverified and come "
            "back as ProcessingFailedError. Install ffprobe on this host.",
            Config.FFMPEG_BIN,
        )


async def inits(app: Application):
    bot: Bot = app.bot
    log_media_pipeline_config()
    tg_owner = await bot.get_chat(chat_id=Config.OWNER_ID)
    with models.session_scope() as s:
        owner = s.get(models.User, tg_owner.id)
        if not owner:
            s.add(
                models.User(
                    user_id=tg_owner.id,
                    username=tg_owner.username if tg_owner.username else "",
                    name=tg_owner.full_name,
                    is_admin=True,
                )
            )


async def set_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st_cmd = ("start", "start command")
    commands = [st_cmd]
    if Admin().filter(update):
        commands.append(("admin", "admin command"))
    await context.bot.set_my_commands(
        commands=commands, scope=BotCommandScopeChat(chat_id=update.effective_chat.id)
    )


@add_new_user
@is_user_banned
@is_user_member
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChat().filter(update):
        await set_commands(update, context)
        lang = get_lang(update.effective_user.id)
        await update.message.reply_text(
            text=TEXTS[lang]["user_welcome_msg"],
            reply_markup=build_user_keyboard(lang),
        )
        return ConversationHandler.END


start_command = CommandHandler(command="start", callback=start)


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PrivateChatAndAdmin().filter(update):
        await set_commands(update, context)
        lang = get_lang(update.effective_user.id)
        await update.message.reply_text(
            text=TEXTS[lang]["admin_welcome_msg"],
            reply_markup=check_hidden_permission_requests_keyboard(
                context=context, admin_id=update.effective_user.id
            ),
        )

        await update.message.reply_text(
            text=TEXTS[lang]["currently_admin"],
            reply_markup=build_admin_keyboard(lang, update.effective_user.id),
        )
        return ConversationHandler.END


admin_command = CommandHandler(command="admin", callback=admin)
