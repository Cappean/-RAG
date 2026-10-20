"""Deterministic structural tests; real BGE tokenizer verification is opt-in."""

import json
import os
import re
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.modules.knowledge.splitter import Block, DocumentSplitter, markdown_blocks, mineru_blocks


def test_textual_toc_is_filtered_per_block_without_dropping_real_prose(tmp_path):
    path = tmp_path / "content_list_v2.json"
    paragraphs = [
        "目录\n1 关于本文 5\n2 简介 5\n3 确诊 6",
        "3.1 检查 7\n3.2 用药 8\n3.3 手术 9",
        "真实正文必须保留。",
        "1 血糖 5\n2 白细胞 6\n3 血小板 7",
    ]
    path.write_text(
        json.dumps(
            [[{"type": "paragraph", "content": {"paragraph_content": text}} for text in paragraphs]]
        )
    )
    blocks = mineru_blocks(path)
    assert [block.text for block in blocks] == paragraphs[2:]


def test_mixed_toc_and_body_in_one_record_preserves_body(tmp_path):
    path = tmp_path / "content_list_v2.json"
    paragraph = "目录\n1 检查 5\n2 手术 6\n这里开始是真实临床正文。"
    path.write_text(
        json.dumps([[{"type": "paragraph", "content": {"paragraph_content": paragraph}}]])
    )
    assert mineru_blocks(path)[0].text == "这里开始是真实临床正文。"


def test_real_cloud_sample_toc_continuations_excluded_and_medical_pages_kept():
    sample = (
        Path(__file__).parents[1] / "data/mineru/DEMO_real_pipeline_sample_20261020_005947_423459"
    )
    path = next(sample.rglob("*content_list_v2.json"), None)
    if path is None:
        pytest.skip("Explicit real cloud acceptance sample is not present")
    blocks = mineru_blocks(path)
    assert not any(block.text.startswith("目录\n") for block in blocks)
    assert not any(
        block.text.startswith(prefix)
        for block in blocks
        for prefix in (
            "6.3.3 消化道重建 13",
            "10 副作用处理 32",
            "11.3.11 70 岁能手术吗",
            "11.7.2 可以吃海参吗",
        )
    )
    assert any("扫码加联络员，审核入群" in block.text for block in blocks)
    medical = [
        block
        for block in blocks
        if any(location.get("pageNumber") == 7 for location in block.locations)
    ]
    assert medical and any("检查" in block.text for block in medical)
    # This assertion isolates paragraph/TOC selection; full mixed-content token
    # budgets are independently tested with the actual BGE tokenizer below.
    chunks = DocumentSplitter(len, target=500, maximum=800).split(
        [block for block in blocks if block.kind == "paragraph"]
    )
    assert not any("目录\n1 关于本文" in chunk["content"] for chunk in chunks)
    assert any(
        location.get("pageNumber") == 7 for chunk in chunks for location in chunk["sourceLocations"]
    )


def test_pdf_cross_page_locations_and_heading_hierarchy(tmp_path):
    path = tmp_path / "content_list_v2.json"
    path.write_text(
        json.dumps(
            [
                [
                    {"type": "title", "content": {"title_content": "3 治疗", "level": 1}},
                    {"type": "title", "content": {"title_content": "3.1 手术", "level": 1}},
                    {
                        "type": "paragraph",
                        "content": {"paragraph_content": "第一段。"},
                        "bbox": [0, 100, 500, 200],
                    },
                ],
                [
                    {
                        "type": "paragraph",
                        "content": {"paragraph_content": "第二段。"},
                        "bbox": [500, 0, 1000, 200],
                    }
                ],
            ]
        ),
        encoding="utf8",
    )
    chunks = DocumentSplitter(len).split(mineru_blocks(path))
    assert len(chunks) == 1
    assert chunks[0]["headingPath"] == ["3 治疗", "3.1 手术"]
    assert chunks[0]["sourceLocations"] == [
        {"pageNumber": 1, "bbox": [0, 0.1, 0.5, 0.2]},
        {"pageNumber": 2, "bbox": [0.5, 0, 1, 0.2]},
    ]


def test_unknown_pdf_structure_is_not_silent_success(tmp_path):
    path = tmp_path / "content_list_v2.json"
    path.write_text('[[{"type":"future_widget","content":{}}]]')
    with pytest.raises(AppError, match="无法可靠识别"):
        mineru_blocks(path)


def test_unverified_normalized_bbox_keeps_page_without_inventing_coordinates(tmp_path):
    path = tmp_path / "content_list_v2.json"
    path.write_text(
        json.dumps(
            [
                [
                    {
                        "type": "paragraph",
                        "content": {"paragraph_content": "test"},
                        "bbox": [0.1, 0.2, 0.3, 0.4],
                    }
                ]
            ]
        )
    )
    assert mineru_blocks(path)[0].locations == [{"pageNumber": 1, "bbox": None}]


def test_markdown_line_ranges_and_fenced_headings():
    blocks = markdown_blocks(
        "# 治疗\r\n\r\n一段\r\n第二行\r\n\r\n```md\r\n# 代码不是标题\r\n```\r\n"
    )
    assert blocks[0].locations == [{"startLine": 3, "endLine": 4}]
    assert blocks[1].locations == [{"startLine": 6, "endLine": 8}]
    assert all(block.headings == ["治疗"] for block in blocks)


def test_long_sentence_keeps_every_character_and_location():
    text = "abcdefghij" * 20
    source = [{"pageNumber": 3, "bbox": None}]
    chunks = DocumentSplitter(len, target=30, maximum=40, overlap=0).split(
        [Block(text, "paragraph", [], source)]
    )
    assert "".join(c["content"] for c in chunks) == text
    assert all(len(c["content"]) <= 40 and c["sourceLocations"] == source for c in chunks)


def test_long_table_preserves_rows_and_repeats_header():
    header = "<tr><th>类别</th></tr>"
    rows = [f"<tr><td>第{i}行</td></tr>" for i in range(8)]
    chunks = DocumentSplitter(len, target=80, maximum=100, overlap=0).split(
        [Block("<table>" + header + "".join(rows) + "</table>", "table", [], [])]
    )
    assert len(chunks) > 1
    assert all("类别" in c["content"] for c in chunks)
    for row in rows:
        label = re.sub(r"<[^>]+>", "", row)
        assert sum(label in c["content"] for c in chunks) == 1


def test_native_markdown_table_preserves_line_mapping():
    text = "# 表格\n\n| 名称 | 内容 |\n| --- | --- |\n| A | 第一行 |\n| B | 第二行 |\n"
    blocks = markdown_blocks(text)
    assert blocks[0].kind == "table"
    assert blocks[0].locations == [{"startLine": 3, "endLine": 6}]
    assert DocumentSplitter(len).split(blocks)[0]["content"] == "".join(text.splitlines(True)[2:])


def test_real_sample_has_no_unsupported_record_types():
    sample = next((Path(__file__).parents[1] / "data/mineru").rglob("*content_list_v2.json"), None)
    if sample is None:
        pytest.skip("Existing real MinerU artifact not present")
    blocks = mineru_blocks(sample)
    assert len(blocks) > 500
    assert {block.kind for block in blocks} >= {"paragraph", "table", "media_text", "footnote"}


@pytest.mark.skipif(
    not os.environ.get("TEST_BGE_MODEL_PATH"), reason="Explicit local model path required"
)
def test_real_bge_tokenizer_sample_budget():
    from transformers import AutoTokenizer

    from app.modules.knowledge.splitter import embedding_text

    tokenizer = AutoTokenizer.from_pretrained(
        os.environ["TEST_BGE_MODEL_PATH"], local_files_only=True
    )

    def count(text):
        return len(tokenizer.encode(text, add_special_tokens=True))

    samples = list((Path(__file__).parents[1] / "data/mineru").rglob("*content_list_v2.json"))
    assert samples
    for sample in samples:
        chunks = DocumentSplitter(count).split(mineru_blocks(sample))
        assert chunks
        assert all(count(embedding_text(c["content"], c["headingPath"])) <= 800 for c in chunks)
