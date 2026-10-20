"""Conversation title rules shared by the online turn, the API and the backfill.

Keeping normalization and the prompt here means a title produced live, renamed by
hand or backfilled cannot drift into different shapes.
"""

import re

DEFAULT_TITLE = "新问诊"
TITLE_MAX_CHARS = 24
TITLE_SOURCE_DEFAULT = "default"
TITLE_SOURCE_AUTO = "auto"
TITLE_SOURCE_USER = "user"

# 标题会落库并显示在列表里，因此不索取患者姓名等标识信息。
TITLE_PROMPT = (
    "你是医疗问诊系统的标题助手。用一句不超过 16 个汉字的中文短语概括这次咨询的主题，"
    "不要引号、书名号、句号、序号或解释，不要包含患者姓名。"
)

_EDGE_PUNCTUATION = "。，、；：！？,.;:!?\"'“”‘’「」『』《》〈〉<>【】[]()（）|｜ "
_MARKDOWN_PREFIX = re.compile(r"^[#*\-\s]+")
# 模型偶尔把整段回答或一句道歉当成标题返回，这些都不能进历史列表。
_REFUSAL_MARKERS = ("抱歉", "对不起", "无法提供", "作为AI", "作为 AI", "我不能", "无法回答")


def title_messages(question: str) -> list[dict]:
    """标题请求的消息体；问题只截前 500 字，避免把整轮上下文送去概括。"""
    return [
        {"role": "system", "content": TITLE_PROMPT},
        {"role": "user", "content": (question or "")[:500]},
    ]


def normalize_title(text: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    """把模型的原始建议收敛成一个短语；返回空串表示不可用。

    模型可能返回带引号的短语、一句完整解释、一份编号清单，甚至一句道歉，
    因此这里先按句读和编号截断，再过滤拒绝类措辞，最后才按长度裁剪。
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = _MARKDOWN_PREFIX.sub("", cleaned).strip(_EDGE_PUNCTUATION)
    # 标题必须是单个短语：遇到句读就截断，避免把后半段解释带进来。
    cleaned = re.split(r"[。！？；!?;]", cleaned)[0].strip(_EDGE_PUNCTUATION)
    # 列表式输出（"要点 1. 确认…"）同样只保留第一段。
    cleaned = re.split(r"\s+\d+[.、)]\s*", cleaned)[0].strip(_EDGE_PUNCTUATION)
    if any(marker in cleaned for marker in _REFUSAL_MARKERS):
        return ""
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip(_EDGE_PUNCTUATION)
    return cleaned


def fallback_title(question: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    """模型不可用时截取首句兜底；截断一定带省略号，不伪装成完整句子。"""
    first = re.split(r"[\n。！？!?]", (question or "").strip())[0]
    compact = re.sub(r"\s+", " ", first).strip()
    if not compact:
        return ""
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "…"


def needs_generated_title(record: dict) -> bool:
    """只有仍是默认标题、且没被用户改过时才允许自动写入。"""
    if record.get("titleSource") == TITLE_SOURCE_USER:
        return False
    return record.get("title") in (None, "", DEFAULT_TITLE)
