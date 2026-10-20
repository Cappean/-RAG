"""Backfill conversation titles that are still on the default placeholder.

One-off and idempotent: only records whose title is still the default and that were
never renamed by hand are touched, so running twice changes nothing. Titles are
clinical content, therefore logs contain ids and lengths only, never title text.
"""

import argparse
import asyncio

from pymongo import AsyncMongoClient

from app.common.titles import (
    DEFAULT_TITLE,
    TITLE_SOURCE_AUTO,
    TITLE_SOURCE_USER,
    fallback_title,
    needs_generated_title,
    normalize_title,
    title_messages,
)
from app.core.config import Settings
from app.core.loggingUtils import get_logger, setup_logging
from app.infrastructure.runtime import DeepSeekModel

logger = get_logger(__name__)


async def build_title(model, question: str, max_chars: int, seconds: float) -> str:
    """模型优先、截取兜底；任何异常都返回可写库的标题而不是抛给调用方。"""
    if not question.strip():
        return ""
    suggestion = ""
    try:
        suggestion = await asyncio.wait_for(
            model.complete(title_messages(question), max_tokens=48), seconds
        )
    except Exception as exc:
        logger.info("title generation failed exception_type=%s", type(exc).__name__)
    return normalize_title(suggestion, max_chars) or fallback_title(question, max_chars)


async def main() -> None:
    parser = argparse.ArgumentParser(description="回填会话标题（幂等，可重复执行）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个会话，0 表示全部")
    # 回填没有用户在等，给比线上更宽的预算，减少无谓的截取回退。
    parser.add_argument("--seconds", type=float, default=10.0, help="单次标题调用超时")
    parser.add_argument(
        "--include-auto", action="store_true", help="连自动生成的标题一起重做（修质量用）"
    )
    args = parser.parse_args()

    settings = Settings()
    setup_logging(settings.log_level)
    mongo = AsyncMongoClient(settings.mongo_uri.get_secret_value(), tz_aware=True)
    model = DeepSeekModel(settings)
    written = skipped = 0
    try:
        database = mongo[settings.mongo_database]
        conversations, messages = database["conversations"], database["messages"]
        query = {"titleSource": {"$ne": TITLE_SOURCE_USER}} if args.include_auto else {
            "title": DEFAULT_TITLE
        }
        rows = await conversations.find(query).to_list(None)
        # 没有 titleSource 字段的老记录一律按 default 处理，但绝不覆盖人工命名。
        targets = [
            row
            for row in rows
            if row.get("titleSource") != TITLE_SOURCE_USER
            and (
                needs_generated_title(row)
                or (args.include_auto and row.get("titleSource") == TITLE_SOURCE_AUTO)
            )
        ]
        if args.limit:
            targets = targets[: args.limit]
        logger.info("backfill candidates total=%s dry_run=%s", len(targets), args.dry_run)

        for row in targets:
            first = await messages.find_one(
                {"conversationId": row["_id"], "role": "user"}, sort=[("createdAt", 1)]
            )
            question = (first or {}).get("content", "")
            title = await build_title(model, question, settings.title_max_chars, args.seconds)
            if not title:
                skipped += 1
                logger.info("backfill skipped conversation_id=%s reason=no_user_text", row["_id"])
                continue
            if args.dry_run:
                written += 1
                logger.info("backfill dry-run conversation_id=%s chars=%s", row["_id"], len(title))
                continue
            await conversations.update_one(
                {"_id": row["_id"]}, {"$set": {"title": title, "titleSource": TITLE_SOURCE_AUTO}}
            )
            written += 1
            logger.info("backfill written conversation_id=%s chars=%s", row["_id"], len(title))
    finally:
        await model.close()
        await mongo.close()
    logger.info(
        "backfill finished written=%s skipped=%s dry_run=%s", written, skipped, args.dry_run
    )


if __name__ == "__main__":
    asyncio.run(main())
