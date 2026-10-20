"""Deterministic MinerU-v2/Markdown splitting with real tokenizer budgets.

Locations always refer to the unmodified input. No model invents headings, pages
or bounding boxes. Unsupported structures fail ingestion instead of disappearing.
"""

import json
import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from app.core.errors import AppError
from app.core.loggingUtils import get_logger

logger = get_logger(__name__)


class _TableRows(HTMLParser):
    """Expand rowspans before chunking so each output row remains interpretable."""

    def __init__(self):
        super().__init__()
        self.rows, self.current, self.cell = [], None, None
        self.spans = {}
        self.max_header_rows = 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current = []
        elif tag in {"td", "th"} and self.current is not None:
            attrs = dict(attrs)
            try:
                self.cell = [
                    tag,
                    int(attrs.get("rowspan", "1")),
                    int(attrs.get("colspan", "1")),
                    "",
                ]
            except ValueError as exc:
                raise _invalid("表格跨度属性无法识别") from exc
            if self.cell[1] < 1 or self.cell[2] < 1:
                raise _invalid("表格跨度属性无效")
        elif self.cell is not None:
            self.cell[3] += self.get_starttag_text()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell[3] += escape(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.current.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.current is not None:
            output, col = [], 0

            def inherited():
                nonlocal col
                while col in self.spans:
                    remaining, width, cell = self.spans[col]
                    output.append(cell)
                    if remaining == 1:
                        del self.spans[col]
                    else:
                        self.spans[col] = (remaining - 1, width, cell)
                    col += width

            for cell_tag, height, width, text in self.current:
                inherited()
                markup = f'<{cell_tag} colspan="{width}">{text}</{cell_tag}>'
                output.append(markup)
                if height > 1:
                    self.spans[col] = (height - 1, width, markup)
                    if not self.rows:
                        self.max_header_rows = max(self.max_header_rows, height)
                col += width
            inherited()
            self.rows.append("<tr>" + "".join(output) + "</tr>")
            self.current = None
        elif self.cell is not None:
            self.cell[3] += f"</{tag}>"


@dataclass
class Block:
    text: str
    kind: str
    headings: list[str]
    locations: list[dict]


def _invalid(message="解析产物含无法可靠识别的结构，未完成入库"):
    return AppError("DOCUMENT_STRUCTURE_UNSUPPORTED", message, 422)


def _text(value):
    """Extract only documented inline text nodes, preserving math/link labels."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    if isinstance(value, dict) and isinstance(value.get("content"), str):
        return value["content"]
    raise _invalid()


def _heading(stack, title, level):
    # Numbered headings take priority over inconsistent PDF visual levels.
    numbered = re.match(r"^(\d+(?:\.\d+)*)(?:[.、\s]|$)", title)
    if numbered:
        depth = len(numbered[1].split("."))
    else:
        depth = level if isinstance(level, int) and level > 0 else 1
    while stack and stack[-1][0] >= depth:
        stack.pop()
    stack.append((depth, title))


def _toc_lines(text: str, active: bool) -> tuple[str, bool]:
    """Recognise a textual TOC without treating its entire section as excluded.

    MinerU sometimes emits a whole two-column TOC as ordinary paragraphs. Require
    explicit TOC provenance and numbered-title/page-number lines, not medical
    keywords or a page-wide heuristic. Any ordinary text terminates continuation.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text, active
    explicit = bool(re.fullmatch(r"目\s*录|Table\s+of\s+Contents|Contents", lines[0], re.I))
    candidates = lines[1:] if explicit else lines

    def entry(line):
        # A trailing printed page, separated from the title by whitespace/dots.
        return bool(re.fullmatch(r".+?(?:\s+|[.．…·]{2,})\d{1,4}", line))

    numbered = sum(bool(re.match(r"^\d+(?:\.\d+)*\s+\S", line)) for line in candidates)
    if (explicit or active) and len(candidates) >= 3 and numbered >= 2:
        if all(entry(line) for line in candidates):
            return "", True
    if explicit or active:
        # Mixed parser blocks can contain TOC rows followed by real prose. Only
        # remove the independently identified rows, never discard the prose.
        index = 0
        while index < len(candidates) and entry(candidates[index]):
            index += 1
        if index >= 2:
            return "\n".join(candidates[index:]), not candidates[index:]
        if explicit and not candidates:
            return "", True
    return text, False


def mineru_blocks(path: Path) -> list[Block]:
    pages = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise _invalid("MinerU content_list_v2 必须是按页排列的二维数组")
    blocks, stack = [], []
    skipped, toc_active = 0, False
    for page_number, records in enumerate(pages, 1):
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("content"), dict):
                raise _invalid()
            kind, data = record.get("type"), record["content"]
            if kind in {"page_header", "page_footer", "page_number", "discarded"}:
                skipped += 1
                continue
            if kind == "title":
                title = _text(data.get("title_content", [])).strip()
                if re.fullmatch(r"目\s*录|Table\s+of\s+Contents|Contents", title, re.I):
                    toc_active = True
                    skipped += 1
                    continue
                if title:
                    toc_active = False
                    _heading(stack, title, data.get("level"))
                continue
            # Explicitly labelled references and TOC are excluded by design.
            if data.get("list_type") in {"reference_list", "catalog", "toc"} or kind in {
                "reference",
                "toc",
            }:
                skipped += 1
                continue
            headings = [item[1] for item in stack]
            if headings and re.match(
                r"^(?:\d+[.、\s]*)?(参考文献|References)\s*$", headings[-1], re.I
            ):
                skipped += 1
                continue
            location = {"pageNumber": page_number, "bbox": None}
            bbox = record.get("bbox")
            if bbox is not None:
                if not (
                    isinstance(bbox, list)
                    and len(bbox) == 4
                    and all(isinstance(v, (int, float)) and 0 <= v <= 1000 for v in bbox)
                    and bbox[0] <= bbox[2]
                    and bbox[1] <= bbox[3]
                ):
                    raise _invalid("MinerU 区域坐标不符合已验证的 0～1000 格式")
                # Tiny/normalised coordinates cannot be distinguished reliably from
                # a different parser coordinate convention. Keep the physical page
                # instead of fabricating a tiny highlight at the page's origin.
                if max(bbox) > 1 and bbox[0] < bbox[2] and bbox[1] < bbox[3]:
                    location["bbox"] = [v / 1000 for v in bbox]
                else:
                    logger.debug("unverified/empty bbox omitted page=%s", page_number)
            texts = []
            if kind == "paragraph":
                paragraph = _text(data.get("paragraph_content", []))
                paragraph, toc_active = _toc_lines(paragraph, toc_active)
                if not paragraph.strip():
                    skipped += 1
                    logger.debug("textual TOC excluded page=%s", page_number)
                    continue
                texts, output_kind = [paragraph], "paragraph"
            elif kind == "list":
                items = data.get("list_items")
                if not isinstance(items, list):
                    raise _invalid()
                texts, output_kind = [_text(item.get("item_content", [])) for item in items], "list"
            elif kind == "table":
                html = data.get("html")
                if not isinstance(html, str) or not re.search(r"<tr\b", html, re.I):
                    raise _invalid("表格缺少可靠 HTML 行列结构")
                texts, output_kind = [html], "table"
                captions = _text(data.get("table_caption", []))
                footnotes = _text(data.get("table_footnote", []))
                for extra in (captions, footnotes):
                    if extra.strip():
                        blocks.append(Block(extra, "footnote", headings.copy(), [location.copy()]))
            elif kind in {"image", "chart"}:
                texts = [
                    _text(data.get("content", ""))
                    + _text(data.get(f"{kind}_caption", []))
                    + _text(data.get(f"{kind}_footnote", []))
                ]
                output_kind = "media_text"
            elif kind in {"page_footnote", "footnote"}:
                texts, output_kind = [_text(data.get(f"{kind}_content", []))], "footnote"
            else:
                logger.info("unsupported parser record page=%s type=%s", page_number, kind)
                raise _invalid()
            for text in texts:
                if text.strip():
                    blocks.append(Block(text, output_kind, headings.copy(), [location.copy()]))
    logger.info(
        "MinerU structures read pages=%s blocks=%s excluded=%s", len(pages), len(blocks), skipped
    )
    return blocks


def markdown_blocks(text: str) -> list[Block]:
    """Keep actual source line ranges, including fenced code and table rows."""
    lines, blocks, stack = text.splitlines(keepends=True), [], []
    start, current, fence = 1, [], None

    def flush(end):
        nonlocal current
        if current and "".join(current).strip():
            content = "".join(current)
            kind = "list" if re.match(r"\s*(?:[-*+] |\d+[.)] )", content) else "paragraph"
            # Native pipe tables retain raw text and original line mapping.
            if len(current) > 1 and re.match(
                r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", current[1]
            ):
                kind = "table"
            blocks.append(
                Block(
                    content,
                    kind,
                    [item[1] for item in stack],
                    [{"startLine": start, "endLine": end}],
                )
            )
        current = []

    for number, line in enumerate(lines, 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        heading = re.match(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$", line)
        if heading and fence is None:
            flush(number - 1)
            _heading(stack, heading[2], len(heading[1]))
            continue
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
        if not line.strip() and fence is None:
            flush(number - 1)
            continue
        if not current:
            start = number
        current.append(line)
    flush(len(lines))
    logger.debug("Markdown structures read lines=%s blocks=%s", len(lines), len(blocks))
    return blocks


def embedding_text(content: str, headings: list[str]) -> str:
    return (" > ".join(headings) + "\n\n" if headings else "") + content


class DocumentSplitter:
    def __init__(self, count_tokens, target=500, maximum=800, overlap=80):
        if not 0 < target <= maximum or not 0 <= overlap < maximum:
            raise ValueError("Invalid chunk token budgets")
        self.count = count_tokens
        self.target, self.maximum, self.overlap = target, maximum, overlap

    def split(self, blocks: list[Block]) -> list[dict]:
        chunks, pending = [], []

        def tokens(text, headings):
            return self.count(embedding_text(text, headings))

        def emit(parts):
            content = "\n\n".join(p.text for p in parts)
            locations = []
            for part in parts:
                for location in part.locations:
                    if location not in locations:
                        locations.append(location)
            chunks.append(
                {
                    "chunkIndex": len(chunks),
                    "contentType": parts[0].kind,
                    "content": content,
                    "headingPath": parts[0].headings.copy(),
                    "sourceLocations": locations,
                }
            )

        for block in blocks:
            if tokens("", block.headings) >= self.maximum:
                raise _invalid("标题路径超过 token 上限，请调整切分配置")
            pieces = self._pieces(block, tokens)
            for piece in pieces:
                if pending and (
                    pending[0].headings != piece.headings
                    or pending[0].kind != piece.kind
                    or piece.kind not in {"paragraph", "list"}
                    or tokens("\n\n".join(p.text for p in pending + [piece]), piece.headings)
                    > self.maximum
                ):
                    emit(pending)
                    pending = []
                pending.append(piece)
                if tokens("\n\n".join(p.text for p in pending), piece.headings) >= self.target:
                    emit(pending)
                    pending = []
        if pending:
            emit(pending)
        if not chunks:
            raise AppError("DOCUMENT_EMPTY", "文档没有可用于检索的文字内容", 422)
        logger.info("document split chunks=%s token_limit=%s", len(chunks), self.maximum)
        return chunks

    def _pieces(self, block, tokens):
        if tokens(block.text, block.headings) <= self.maximum:
            return [block]
        if block.kind == "table":
            if not block.text.lstrip().lower().startswith("<table"):
                rows = block.text.splitlines(keepends=True)
                header, groups, current = "".join(rows[:2]), [], []
                for row in rows[2:]:
                    if tokens(header + "".join(current) + row, block.headings) > self.maximum:
                        if current:
                            groups.append(header + "".join(current))
                            current = []
                    if tokens(header + row, block.headings) > self.maximum:
                        raise _invalid("Markdown 表格单行超过 token 上限")
                    current.append(row)
                if current:
                    groups.append(header + "".join(current))
                if not groups:
                    raise _invalid("Markdown 表头超过 token 上限")
                return [Block(t, block.kind, block.headings, block.locations) for t in groups]
            parser = _TableRows()
            parser.feed(block.text)
            rows = parser.rows
            if not rows or parser.spans:
                raise _invalid("超长表格含不完整跨行单元格")
            header_rows = min(parser.max_header_rows, len(rows))
            header, groups, current = "".join(rows[:header_rows]), [], []
            if header_rows == len(rows):
                raise _invalid("表格表头超过 token 上限")
            for row in rows[header_rows:]:
                candidate = "<table>" + header + "".join(current + [row]) + "</table>"
                if tokens(candidate, block.headings) > self.maximum and current:
                    groups.append("<table>" + header + "".join(current) + "</table>")
                    current = []
                if tokens("<table>" + header + row + "</table>", block.headings) > self.maximum:
                    raise _invalid("表格单行超过 token 上限，无法保持完整行列结构")
                current.append(row)
            if current:
                groups.append("<table>" + header + "".join(current) + "</table>")
            return [Block(t, block.kind, block.headings, block.locations) for t in groups]
        # Split at sentence boundaries first, then by a verified character prefix.
        sentences = re.findall(r".*?(?:[。！？!?；;](?:\s*)|$)", block.text, re.S)
        pieces = []
        for sentence in filter(None, sentences):
            while tokens(sentence, block.headings) > self.maximum:
                low, high = 1, len(sentence)
                while low < high:
                    middle = (low + high + 1) // 2
                    if tokens(sentence[:middle], block.headings) <= self.maximum:
                        low = middle
                    else:
                        high = middle - 1
                if tokens(sentence[:low], block.headings) > self.maximum:
                    raise _invalid("单字符与标题超过 token 上限")
                pieces.append(sentence[:low])
                sentence = sentence[low:]
            if sentence:
                pieces.append(sentence)
        return [Block(t, block.kind, block.headings, block.locations) for t in pieces]
