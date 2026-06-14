from __future__ import annotations

from dataclasses import dataclass
import re
from functools import reduce
from math import gcd
from typing import Iterable, List, Literal, Tuple, Optional

TocMode = Literal["numbering", "indent", "auto"]

from pypdf import PdfReader, PdfWriter


@dataclass
class Heading:
    title: str
    page: int  # 1-based
    level: int  # 1..6


def generate_bookmarks(src_pdf: str, out_pdf: str, headings: Iterable[Heading]) -> None:
    """Write given headings into a new PDF file as outline/bookmarks."""
    reader = PdfReader(src_pdf)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    # Build hierarchical outlines using a simple stack by levels
    stack: List[Tuple[int, object]] = []  # (level, parent_ref)

    for h in headings:
        page_index = max(0, min(len(reader.pages) - 1, h.page - 1))
        while stack and stack[-1][0] >= h.level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        dest = writer.add_outline_item(h.title, page_index, parent=parent)
        stack.append((h.level, dest))

    with open(out_pdf, "wb") as f:
        writer.write(f)


# -------------------- TOC parsing utilities --------------------

_NUM_PREFIX_RE = re.compile(
    r"^\s*(?P<num>(第\s*\d+[一二三四五六七八九十百千]*[章节节部分编]?)|((\d+\.)+\d+)|\d+)?\s*"
)
_TRAILING_PAGE_RE = re.compile(r"(?P<page>\d{1,5})\s*$")


def _infer_level_from_numbering(num: Optional[str]) -> int:
    if not num:
        return 1
    num = num.strip()
    if num.startswith("第"):
        # "第1章" style => top-level
        return 1
    if "." in num:
        # "1.2.3" => level = segments + 1 (so 1.2 is level 2)
        return min(6, max(1, num.count(".") + 1))
    # Simple leading integer like "1" => level 1
    return 1


def _leading_indent_width(raw_line: str) -> int:
    width = 0
    for ch in raw_line:
        if ch == " ":
            width += 1
        elif ch == "\t":
            width += 4
        else:
            break
    return width


def _detect_indent_unit(indents: Iterable[int]) -> int:
    non_zero = sorted({i for i in indents if i > 0})
    if not non_zero:
        return 4
    unit = non_zero[0]
    if all(i % unit == 0 for i in indents):
        return max(1, unit)
    return max(1, reduce(gcd, non_zero))


def _infer_level_from_indent(indent: int, unit: int) -> int:
    if indent <= 0:
        return 1
    return min(6, max(1, indent // unit + 1))


def _strip_star_prefix(line: str) -> Tuple[str, str]:
    star_prefix = ""
    m_star = re.match(r"^\*+\s*", line)
    if m_star:
        star_prefix = "*" * m_star.group(0).count("*")
        line = line[m_star.end() :].lstrip()
    return star_prefix, line


def _detect_toc_mode(toc_text: str, min_len: int = 1) -> TocMode:
    """Auto-detect whether TOC hierarchy is expressed by numbering or indentation."""
    indent_signals = 0
    numbering_signals = 0
    for raw_line in toc_text.splitlines():
        if len(raw_line.strip()) < min_len:
            continue
        line = raw_line.lstrip()
        _, line = _strip_star_prefix(line)
        page_m = _TRAILING_PAGE_RE.search(line)
        if not page_m:
            continue
        line_wo_page = line[: page_m.start()].rstrip()
        indent = _leading_indent_width(raw_line)
        num_m = _NUM_PREFIX_RE.match(line_wo_page)
        has_numbering = bool(num_m and num_m.group("num"))
        if has_numbering:
            numbering_signals += 1
        elif indent > 0:
            indent_signals += 1
    return "indent" if indent_signals > numbering_signals else "numbering"


def _parse_toc_lines_numbering(
    toc_text: str, page_offset: int = 0, min_len: int = 1
) -> List[Heading]:
    headings: List[Heading] = []
    for raw_line in toc_text.splitlines():
        line = raw_line.strip()
        if len(line) < min_len:
            continue
        star_prefix, line = _strip_star_prefix(line)

        page_m = _TRAILING_PAGE_RE.search(line)
        if not page_m:
            continue
        page_num = int(page_m.group("page"))
        line_wo_page = line[: page_m.start()].rstrip()
        num_m = _NUM_PREFIX_RE.match(line_wo_page)
        numbering = None
        title_part = line_wo_page
        if num_m:
            numbering = num_m.group("num")
            title_part = line_wo_page[num_m.end() :].strip()
        if numbering:
            combined = f"{numbering.strip()} {title_part}".strip()
        else:
            combined = title_part
        title = re.sub(r"\s+", " ", combined)
        if not title:
            title = line_wo_page.strip()
        if star_prefix:
            title = f"{star_prefix}{title}".strip()
        level = _infer_level_from_numbering(numbering)
        pdf_page = max(1, page_num + page_offset)
        headings.append(Heading(title=title, page=pdf_page, level=level))

    headings.sort(key=lambda h: (h.page, h.level, h.title.lower()))
    return headings


def _parse_toc_lines_indent(toc_text: str, page_offset: int = 0, min_len: int = 1) -> List[Heading]:
    lines_data: List[Tuple[int, str, int]] = []
    indents: List[int] = []
    for raw_line in toc_text.splitlines():
        if len(raw_line.strip()) < min_len:
            continue
        indent = _leading_indent_width(raw_line)
        line = raw_line.lstrip()
        star_prefix, line = _strip_star_prefix(line)

        page_m = _TRAILING_PAGE_RE.search(line)
        if not page_m:
            continue
        page_num = int(page_m.group("page"))
        title = re.sub(r"\s+", " ", line[: page_m.start()].rstrip())
        if star_prefix:
            title = f"{star_prefix}{title}".strip()
        indents.append(indent)
        lines_data.append((indent, title, page_num))

    unit = _detect_indent_unit(indents)
    headings: List[Heading] = []
    for indent, title, page_num in lines_data:
        level = _infer_level_from_indent(indent, unit)
        pdf_page = max(1, page_num + page_offset)
        headings.append(Heading(title=title, page=pdf_page, level=level))

    headings.sort(key=lambda h: (h.page, h.level, h.title.lower()))
    return headings


def parse_toc_lines(
    toc_text: str,
    page_offset: int = 0,
    min_len: int = 1,
    mode: TocMode = "auto",
) -> List[Heading]:
    """
    Parse a pasted TOC text into Heading entries.
    - Each line should end with the book page number (digits)
    - mode="numbering": hierarchy from leading numbers like "1", "1.1", "第1章"
    - mode="indent": hierarchy from leading spaces/tabs
    - mode="auto": detect numbering vs indent automatically
    - page_offset is added to the parsed page number to map to PDF actual pages
    """
    resolved_mode = _detect_toc_mode(toc_text, min_len) if mode == "auto" else mode
    if resolved_mode == "indent":
        return _parse_toc_lines_indent(toc_text, page_offset=page_offset, min_len=min_len)
    return _parse_toc_lines_numbering(toc_text, page_offset=page_offset, min_len=min_len)


## URL/website TOC fetching intentionally removed; only manual text input is supported.


