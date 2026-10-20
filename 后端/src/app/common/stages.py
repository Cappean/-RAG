"""Canonical progress stage identifiers shared by SSE streams and task records.

The identifiers are the wire contract; human-readable labels live in the client so
wording can change without breaking persisted or in-flight payloads. Every stage
list is ordered: a client renders all stages up to the current one as finished and
the remainder as pending.
"""

from collections.abc import Iterable

# 问诊：整理问诊信息 → 检索指南 → 整理引用 → 生成回答 → 完成
CONSULTATION_STAGES: tuple[str, ...] = ("intake", "retrieval", "citation", "generation", "done")

# 知识库入库：等待处理 → 解析文档 → 切分内容 → 生成向量并入库 → 完成
KNOWLEDGE_STAGES: tuple[str, ...] = ("queued", "parse", "split", "vectorize", "done")

# 原生 Markdown 没有独立的解析步骤，由任务在入队后声明为“不适用”。
MARKDOWN_SKIPPED_STAGES: tuple[str, ...] = ("parse",)


def known_stage(stage: str | None, stages: Iterable[str]) -> bool:
    """Guard against typos in stage identifiers before they reach a client."""
    return stage in set(stages)
