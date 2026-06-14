from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

from .core import TocMode, generate_bookmarks, parse_toc_lines

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # Python 3.9-3.10
    try:
        import tomli as tomllib  # type: ignore[assignment]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="tocsmith", description="Auto add bookmarks to PDF")
    p.add_argument("pdf", nargs="?", help="Input PDF path")
    p.add_argument("-o", "--out", help="Output PDF path; default: <name>.bookmarked.pdf")
    p.add_argument("--min-len", type=int, default=3, help="Minimum heading text length")
    p.add_argument("--page-offset", type=int, default=0, help="Page offset: actual - book page")
    p.add_argument("--toc-file", help="Path to a text file containing TOC lines")
    p.add_argument(
        "--toc-mode",
        choices=["auto", "numbering", "indent"],
        default="auto",
        help="TOC hierarchy mode: numbering (1/1.1), indent (spaces), or auto-detect",
    )
    p.add_argument(
        "-c",
        "--config",
        help="Path to a TOML config file for batch tasks (overrides single-run args)",
    )
    return p.parse_args(argv)


def _resolve_relative(base_dir: Path, maybe_path: Optional[str]) -> Optional[Path]:
    """Resolve a path relative to base_dir if provided; return None if empty."""
    if not maybe_path:
        return None
    p = Path(maybe_path)
    return (base_dir / p).resolve() if not p.is_absolute() else p


def _run_single(
    src: Path,
    out: Optional[Path],
    toc_file: Optional[Path],
    page_offset: int,
    min_len: int,
    toc_text: Optional[str] = None,
    toc_mode: TocMode = "auto",
) -> int:
    """Run a single task and return process exit code."""
    if not src.exists():
        print(f"File not found: {src}")
        return 2
    out_path = out if out else src.with_suffix(".bookmarked.pdf")

    headings = []
    if toc_text is not None and toc_text.strip():
        headings = parse_toc_lines(
            toc_text, page_offset=page_offset, min_len=min_len, mode=toc_mode
        )
    elif toc_file:
        file_text = Path(toc_file).read_text(encoding="utf-8")
        headings = parse_toc_lines(
            file_text, page_offset=page_offset, min_len=min_len, mode=toc_mode
        )
    else:
        print("No TOC source provided (use --toc-file). Producing a copy without outline.")
        headings = []
    if not headings:
        print("No headings; output will be a copy without outline.")
    generate_bookmarks(str(src), str(out_path), headings)
    print(f"Wrote: {out_path}")
    return 0


def _run_batch(config_path: Path) -> int:
    '''Run batch tasks from a TOML config file.

    Config schema (customized):
    [defaults]
    page_offset = 0
    min_len = 3
    input_prefix = "input"              # optional; base dir for input files
    output_prefix = "output"            # optional; base dir for outputs
    output_suffix = ".bookmarked.pdf"   # optional; appended to stem

    [[tasks]]
    input_file = "book1.pdf"            # required; relative to input_prefix
    toc = """..."""                     # optional inline TOC text
    # Alternatively: toc_file = "toc.txt"
    page_offset = 10                     # optional overrides default
    min_len = 2                          # optional overrides default
    toc_mode = "auto"                    # optional: auto | numbering | indent
    '''
    if tomllib is None:
        print("Error: TOML support not available. Please install 'tomli' for Python < 3.11.")
        return 2

    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        return 2

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    base_dir = config_path.parent
    defaults: Dict[str, Any] = data.get("defaults", {}) or {}
    tasks: List[Dict[str, Any]] = data.get("tasks", []) or []
    if not isinstance(tasks, list) or not tasks:
        print("No tasks found in config (expected [[tasks]] array)")
        return 2

    default_page_offset = int(defaults.get("page_offset", 0) or 0)
    default_min_len = int(defaults.get("min_len", 3) or 3)
    default_toc_mode = str(defaults.get("toc_mode", "auto") or "auto").strip() or "auto"
    if default_toc_mode not in ("auto", "numbering", "indent"):
        print(f"Invalid defaults.toc_mode: {default_toc_mode!r}")
        return 2
    input_prefix = str(defaults.get("input_prefix", "")).strip() or ""
    output_prefix = str(defaults.get("output_prefix", "")).strip() or ""
    output_suffix = (
        str(defaults.get("output_suffix", ".bookmarked.pdf")).strip() or ".bookmarked.pdf"
    )

    input_base = (base_dir / input_prefix).resolve() if input_prefix else base_dir
    output_base = (base_dir / output_prefix).resolve() if output_prefix else base_dir

    failures = 0
    for idx, t in enumerate(tasks, start=1):
        input_file_val = t.get("input_file")
        if not input_file_val:
            print(f"[Task {idx}] Skipped: missing 'input_file'")
            failures += 1
            continue

        # Resolve input file relative to input_base
        src = (input_base / str(input_file_val)).resolve()

        # Determine output path {output_base}/{stem}{output_suffix}
        try:
            out_stem = Path(str(input_file_val)).stem
        except Exception:
            out_stem = "output"
        out = (output_base / f"{out_stem}{output_suffix}").resolve()

        # Obtain TOC from inline 'toc' or optional 'toc_file' fallback
        toc_inline: Optional[str] = t.get("toc")
        toc_file = _resolve_relative(base_dir, t.get("toc_file"))
        page_offset = int(t.get("page_offset", default_page_offset) or default_page_offset)
        min_len = int(t.get("min_len", default_min_len) or default_min_len)
        toc_mode = str(t.get("toc_mode", default_toc_mode) or default_toc_mode).strip() or "auto"
        if toc_mode not in ("auto", "numbering", "indent"):
            print(f"[Task {idx}] Skipped: invalid toc_mode {toc_mode!r}")
            failures += 1
            continue

        print(
            f"[Task {idx}] Running: src={src} out={out} "
            f"toc={'inline' if (toc_inline and toc_inline.strip()) else (toc_file or '<none>')} "
            f"offset={page_offset} min_len={min_len} toc_mode={toc_mode}"
        )
        try:
            # Ensure output directory exists
            out.parent.mkdir(parents=True, exist_ok=True)
            code = _run_single(
                src=Path(src),
                out=out,
                toc_file=toc_file,
                page_offset=page_offset,
                min_len=min_len,
                toc_text=toc_inline,
                toc_mode=toc_mode,  # type: ignore[arg-type]
            )
            if code != 0:
                failures += 1
        except Exception as e:
            failures += 1
            print(f"[Task {idx}] Failed: {e}")

    if failures:
        print(f"Completed with {failures} failure(s)")
        return 1
    print("All tasks completed successfully")
    return 0


def main(argv: List[str] | None = None) -> int:
    ns = parse_args(argv)
    if ns.config:
        return _run_batch(Path(ns.config))

    if not ns.pdf:
        print("Error: either specify a PDF or use --config for batch mode.")
        return 2

    src = Path(ns.pdf)
    out = Path(ns.out) if ns.out else None
    return _run_single(
        src=src,
        out=out,
        toc_file=Path(ns.toc_file) if ns.toc_file else None,
        page_offset=ns.page_offset,
        min_len=ns.min_len,
        toc_mode=ns.toc_mode,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


