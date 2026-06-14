from pathlib import Path
import re

import pytest

from tocsmith.core import Heading, generate_bookmarks, parse_toc_lines
from tocsmith import cli
import textwrap


@pytest.fixture()
def tmp_pdf(tmp_path: Path) -> Path:
    # Create a minimal single-page PDF using pypdf
    from pypdf import PdfWriter

    out = tmp_path / "a.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)  # A4
    with out.open("wb") as f:
        w.write(f)
    return out


def test_generate_bookmarks_no_headings(tmp_pdf: Path, tmp_path: Path):
    out = tmp_path / "out.pdf"
    generate_bookmarks(str(tmp_pdf), str(out), [])
    assert out.exists() and out.stat().st_size > 0


def test_generate_bookmarks_with_headings(tmp_pdf: Path, tmp_path: Path):
    out = tmp_path / "out.pdf"
    hs = [Heading(title="Intro", page=1, level=1)]
    generate_bookmarks(str(tmp_pdf), str(out), hs)
    assert out.exists() and out.stat().st_size > 0


def test_no_auto_analysis_copy_only(tmp_pdf: Path):
    # Without headings, we can still generate a copy
    from pypdf import PdfReader
    out = tmp_pdf.with_name("copy.pdf")
    generate_bookmarks(str(tmp_pdf), str(out), [])
    r = PdfReader(str(out))
    assert len(r.pages) == 1


def test_parse_toc_lines_basic_offset():
    toc = """
    第1章 基础 1
    1.1 Scala解释器 3
    1.2 声明值和变量 4
    2 进阶 10
    """.strip()
    hs = parse_toc_lines(toc, page_offset=14)
    assert [h.page for h in hs] == [15, 17, 18, 24]
    # Ensure titles exist and have reasonable levels
    assert hs[0].level == 1
    assert hs[1].level >= 2


def test_parse_toc_lines_robust_trailing_spaces_and_tabs():
    toc = "\n".join([
        "第1章   基础\t 1",
        " 1.1\tScala解释器 \t 3 ",
        "附录 A  100",
    ])
    hs = parse_toc_lines(toc, page_offset=0)
    assert hs[0].page == 1
    assert hs[1].page == 3
    # When no numeric prefix (like "附录 A"), default to level 1
    assert any(h.title.startswith("附录") and h.level == 1 for h in hs)


def test_parse_toc_lines_preserve_asterisk_prefix():
    toc = "\n".join([
        "*1.1 subdirectory 12",
        "* 1.2 another subdirectory 13",
        "1.3 normal 14",
    ])
    hs = parse_toc_lines(toc, page_offset=0)
    titles = [h.title for h in hs]
    assert titles[0].startswith("*") and "subdirectory" in titles[0]
    assert titles[1].startswith("*") and "another subdirectory" in titles[1]
    assert not titles[2].startswith("*")


def test_batch_config_custom_format(tmp_path: Path, monkeypatch):
    # Arrange input/output structure
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_pdf = input_dir / "book1.pdf"
    input_dir.mkdir(parents=True, exist_ok=True)
    # Create a tiny but valid one-page PDF for reading
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with input_pdf.open("wb") as f:
        writer.write(f)

    config_text = textwrap.dedent('''
        [defaults]
        page_offset = 1
        min_len = 1
        input_prefix = "input"
        output_prefix = "output"
        output_suffix = ".bookmarked.pdf"

        [[tasks]]
        input_file = "book1.pdf"
        toc = """
        第一章 绪论 1
        1.1 引言 2
        """
        page_offset = 2
        min_len = 1
        ''').strip()

    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text, encoding="utf-8")

    # Capture calls to generate_bookmarks
    captured = {}

    def fake_generate(src: str, out: str, headings):
        captured["src"] = Path(src)
        captured["out"] = Path(out)
        captured["headings"] = list(headings)

    monkeypatch.setattr(cli, "generate_bookmarks", fake_generate)

    # Act
    code = cli._run_batch(config_path)

    # Assert
    assert code == 0
    assert captured["src"].resolve() == input_pdf.resolve()
    assert captured["out"].resolve() == (output_dir / "book1.bookmarked.pdf").resolve()
    assert len(captured["headings"]) == 2


def test_parse_toc_lines_preserve_numbering_prefix_in_title():
    toc = "\n".join([
        "第1章 计算机系统概述 1",
        "1.1 操作系统的基本概念 2",
        "2 其他章节 10",
    ])
    hs = parse_toc_lines(toc, page_offset=0)
    titles = [h.title for h in hs]
    # Ensure numbering like "第1章" and "1.1" are preserved in the final title
    assert any(t.startswith("第1章 ") and "计算机系统概述" in t for t in titles)
    assert any(t.startswith("1.1 ") and "操作系统的基本概念" in t for t in titles)
    assert any(t.startswith("2 ") and "其他章节" in t for t in titles)


def test_parse_toc_lines_preserve_numbering_with_asterisk():
    toc = "\n".join([
        "*1.1 星标小节 12",
        "* 2 星标章节 13",
    ])
    hs = parse_toc_lines(toc, page_offset=0)
    titles = [h.title for h in hs]
    # Star prefix should precede the numbering, and numbering should remain
    assert any(t.startswith("*1.1 ") and "星标小节" in t for t in titles)
    assert any(t.startswith("*2 ") and "星标章节" in t for t in titles)


def test_parse_toc_lines_numbering_mode_example():
    toc = "\n".join([
        "1  我是标题  1",
        "1.1  我是子标题  2",
        "1.1.1  我是子子标题  3",
    ])
    hs = parse_toc_lines(toc, page_offset=0, mode="numbering")
    assert len(hs) == 3
    assert hs[0].title == "1 我是标题"
    assert hs[0].level == 1
    assert hs[1].title == "1.1 我是子标题"
    assert hs[1].level == 2
    assert hs[2].title == "1.1.1 我是子子标题"
    assert hs[2].level == 3


def test_parse_toc_lines_indent_mode_example():
    toc = "\n".join([
        "我是标题  1",
        "    我是子标题  2",
        "        我是子子标题  3",
    ])
    hs = parse_toc_lines(toc, page_offset=0, mode="indent")
    assert len(hs) == 3
    assert hs[0].title == "我是标题"
    assert hs[0].level == 1
    assert hs[1].title == "我是子标题"
    assert hs[1].level == 2
    assert hs[2].title == "我是子子标题"
    assert hs[2].level == 3


def test_parse_toc_lines_auto_detect_indent():
    toc = "\n".join([
        "Chapter A 1",
        "    Section B 2",
        "        Subsection C 3",
    ])
    hs = parse_toc_lines(toc, page_offset=0, mode="auto")
    assert [h.level for h in hs] == [1, 2, 3]


def test_parse_toc_lines_strip_numbering_when_disabled():
    toc = "\n".join([
        "第1章 计算机系统概述 1",
        "1.1 操作系统的基本概念 2",
        "2 其他章节 10",
    ])
    hs = parse_toc_lines(toc, page_offset=0, keep_numbering=False)
    titles = [h.title for h in hs]
    assert any(t == "计算机系统概述" for t in titles)
    assert any(t == "操作系统的基本概念" for t in titles)
    assert any(t == "其他章节" for t in titles)
    assert [h.level for h in hs] == [1, 2, 1]


def test_parse_toc_lines_strip_numbering_with_asterisk():
    toc = "\n".join([
        "*1.1 星标小节 12",
        "* 2 星标章节 13",
    ])
    hs = parse_toc_lines(toc, page_offset=0, keep_numbering=False)
    titles = [h.title for h in hs]
    assert any(t == "*星标小节" for t in titles)
    assert any(t == "*星标章节" for t in titles)


def test_parse_toc_lines_auto_detect_numbering():
    toc = "\n".join([
        "第1章 基础 1",
        "1.1 小节 2",
    ])
    hs = parse_toc_lines(toc, page_offset=0, mode="auto")
    assert hs[0].level == 1
    assert hs[1].level == 2
    assert hs[0].title.startswith("第1章")
    assert hs[1].title.startswith("1.1")


