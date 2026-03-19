from telegram.ext import ContextTypes
from meta.publishers import publish_to_meta
from common.lang_dicts import TEXTS
import models


async def schedule_publish_to_meta(context: ContextTypes.DEFAULT_TYPE):
    meta_post_id = context.job.data.get("meta_post_id")
    lang = context.job.data.get("lang") or models.Language.ARABIC

    try:
        result_text = await publish_to_meta(
            payload=context.job.data["payload"], context=context
        )

        if meta_post_id:
            with models.session_scope() as s:
                meta_post = s.get(models.MetaPost, meta_post_id)
                if meta_post:
                    meta_post.status = "published"
                    meta_post.meta_response = result_text
                    meta_post.last_error = None

        await context.bot.send_message(
            chat_id=context.job.user_id,
            text=result_text,
        )
    except Exception as e:
        if meta_post_id:
            with models.session_scope() as s:
                meta_post = s.get(models.MetaPost, meta_post_id)
                if meta_post:
                    meta_post.status = "failed"
                    meta_post.meta_response = None
                    meta_post.last_error = str(e)

        await context.bot.send_message(
            chat_id=context.job.user_id,
            text=TEXTS[lang][
                "meta_upload_publish_failed"
            ].format(err=str(e)),
        )
