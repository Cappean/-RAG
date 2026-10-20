"""标题规则单测：模型给的各种脏输出都要收敛成可展示的短语。"""

from app.common.titles import (
    DEFAULT_TITLE,
    fallback_title,
    needs_generated_title,
    normalize_title,
)


def test_normalize_strips_quotes_and_trailing_punctuation():
    assert normalize_title("「胃癌术后辅助化疗」") == "胃癌术后辅助化疗"
    assert normalize_title("胃癌术后辅助化疗。") == "胃癌术后辅助化疗"
    assert normalize_title("《胃癌术后辅助化疗》") == "胃癌术后辅助化疗"
    # 模型偶尔在标题末尾回带表格竖线。
    assert normalize_title("S-1单药与CAPOX对比 |") == "S-1单药与CAPOX对比"


def test_normalize_keeps_only_the_first_phrase():
    # 模型把整段解释或编号清单当标题返回时，只保留第一段。
    assert normalize_title("化疗前需确认的信息。包括病理分期与用药方案") == "化疗前需确认的信息"
    assert normalize_title("化疗前确认要点 1. 确认分期 2. 确认用药") == "化疗前确认要点"


def test_normalize_rejects_refusals_and_apologies():
    # 这类文本进列表比没有标题更糟，交给首句截取兜底。
    assert normalize_title("抱歉，无法提供该信息") == ""
    assert normalize_title("作为AI，我不能给出诊断") == ""


def test_normalize_truncates_long_single_phrase():
    assert len(normalize_title("胃癌术后辅助化疗前需要确认的病理分期与用药方案细节")) == 24


def test_fallback_marks_truncation_with_ellipsis():
    question = "第二个周期化疗什么时候开始比较合适呢请问"
    title = fallback_title(question, 10)
    assert title.endswith("…")
    assert len(title) == 11
    assert fallback_title("第一句？第二句", 20) == "第一句"


def test_generated_title_respects_user_rename():
    assert needs_generated_title({"title": DEFAULT_TITLE}) is True
    assert needs_generated_title({"title": "我的名字", "titleSource": "user"}) is False
    assert needs_generated_title({"title": "已有自动标题", "titleSource": "auto"}) is False
