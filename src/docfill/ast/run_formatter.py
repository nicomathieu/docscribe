"""Formatting-preservation utilities for DOCX text runs.

The core insight: an LLM produces plain text, but the DOCX template carries
run-level formatting (bold labels, italic annotations, fonts). This module
reconstructs a run list from new text + template runs so that **bold labels
are preserved** while LLM-generated values remain plain.

Algorithm overview:
1. Find the longest common prefix between new text and original template text.
2. Map that prefix to the template runs (preserving their bold/italic/underline).
3. For the remaining (new) text, try sequential matching against template runs.
4. For any leftover unmatched text, scan for known bold-label fragments and
   wrap them in bold; everything else becomes plain.
"""

from __future__ import annotations

from docfill.ast.models import TextRun


# ---------------------------------------------------------------------------
# Step 1 — common prefix
# ---------------------------------------------------------------------------

def find_common_prefix(text1: str, text2: str) -> int:
    common = 0
    for i in range(min(len(text1), len(text2))):
        if text1[i] == text2[i]:
            common = i + 1
        else:
            break
    return common


# ---------------------------------------------------------------------------
# Step 2 — map prefix to template runs
# ---------------------------------------------------------------------------

def _map_prefix_to_template_runs(
    text: str,
    common_prefix_len: int,
    template_runs: list[TextRun],
    font_name: str,
    font_size_pt: float,
) -> list[TextRun]:
    result: list[TextRun] = []
    char_pos = 0

    for trun in template_runs:
        if char_pos >= common_prefix_len:
            break

        run_text = trun.text or ""
        run_end = min(char_pos + len(run_text), common_prefix_len)
        slice_text = text[char_pos:run_end]

        if slice_text:
            trun_bold = trun.bold or False
            trun_italic = trun.italic or False
            trun_underline = trun.underline

            if (
                not result
                or result[-1].bold != trun_bold
                or result[-1].italic != trun_italic
                or result[-1].underline != trun_underline
            ):
                result.append(
                    TextRun(
                        text=slice_text,
                        bold=trun_bold,
                        italic=trun_italic,
                        underline=trun_underline,
                        font_name=trun.font_name or font_name,
                        font_size_pt=trun.font_size_pt or font_size_pt,
                    )
                )
            else:
                result[-1].text += slice_text

        char_pos += len(run_text)

    return result


# ---------------------------------------------------------------------------
# Steps 3-4 helpers
# ---------------------------------------------------------------------------

def _find_prefix_end_run_index(
    common_prefix_len: int, template_runs: list[TextRun]
) -> int | None:
    if common_prefix_len == 0:
        return None
    pos = 0
    for idx, trun in enumerate(template_runs):
        pos += len(trun.text or "")
        if pos > common_prefix_len:
            return idx
    return None


def _build_remaining_runs(
    common_prefix_len: int, template_runs: list[TextRun]
) -> list[tuple[str, TextRun]]:
    remaining: list[tuple[str, TextRun]] = []
    pos = 0
    for trun in template_runs:
        run_text = trun.text or ""
        if pos + len(run_text) > common_prefix_len:
            if pos < common_prefix_len:
                tail = run_text[common_prefix_len - pos :]
                if tail:
                    remaining.append((tail, trun))
            else:
                remaining.append((run_text, trun))
        pos += len(run_text)
    return remaining


def _try_sequential_matching(
    text: str,
    remaining_runs: list[tuple[str, TextRun]],
    result: list[TextRun],
    font_name: str,
    font_size_pt: float,
) -> tuple[int, int]:
    pos = 0
    matched = 0
    for run_text, trun in remaining_runs:
        if pos >= len(text):
            break
        if text[pos:].startswith(run_text):
            _add_run(
                result,
                run_text,
                trun.bold or False,
                trun.italic or False,
                trun.underline,
                trun.font_name or font_name,
                trun.font_size_pt or font_size_pt,
            )
            pos += len(run_text)
            matched += 1
        else:
            break
    return pos, matched


def _build_search_patterns(
    remaining_runs: list[tuple[str, TextRun]],
    font_name: str,
    font_size_pt: float,
) -> list[tuple[str, dict]]:
    patterns: list[tuple[str, dict]] = []

    def _fmt(trun: TextRun) -> dict:
        return {
            "bold": trun.bold or False,
            "italic": trun.italic or False,
            "underline": trun.underline,
            "font_name": trun.font_name or font_name,
            "font_size_pt": trun.font_size_pt or font_size_pt,
        }

    for run_text, trun in remaining_runs:
        if run_text.strip() and len(run_text.strip()) > 1:
            patterns.append((run_text, _fmt(trun)))

    for i in range(len(remaining_runs) - 1):
        rt1, tr1 = remaining_runs[i]
        rt2, tr2 = remaining_runs[i + 1]
        if tr1.bold and tr2.bold and rt1.strip() and rt2.strip():
            patterns.append((rt1 + rt2, {**_fmt(tr1), "bold": True}))

    for i in range(len(remaining_runs) - 2):
        rt1, tr1 = remaining_runs[i]
        rt2, tr2 = remaining_runs[i + 1]
        rt3, tr3 = remaining_runs[i + 2]
        if all(r.bold for r in (tr1, tr2, tr3)) and all(
            t.strip() for t in (rt1, rt2, rt3)
        ):
            patterns.append((rt1 + rt2 + rt3, {**_fmt(tr1), "bold": True}))

    return sorted(patterns, key=lambda x: len(x[0]), reverse=True)


def _add_run(
    result: list[TextRun],
    text: str,
    bold: bool,
    italic: bool,
    underline: bool | None,
    font_name: str,
    font_size_pt: float,
) -> None:
    if not result or result[-1].bold != bold or result[-1].italic != italic or result[-1].underline != underline:
        result.append(
            TextRun(
                text=text,
                bold=bold,
                italic=italic,
                underline=underline,
                font_name=font_name,
                font_size_pt=font_size_pt,
            )
        )
    else:
        result[-1].text += text


def _default_format(
    prefix_end_idx: int | None,
    template_runs: list[TextRun],
    font_name: str,
    font_size_pt: float,
) -> dict:
    if prefix_end_idx is not None and prefix_end_idx < len(template_runs):
        trun = template_runs[prefix_end_idx]
        if trun.bold and prefix_end_idx + 1 < len(template_runs):
            nxt = template_runs[prefix_end_idx + 1]
            return {
                "bold": False,
                "italic": False,
                "underline": None,
                "font_name": nxt.font_name or font_name,
                "font_size_pt": nxt.font_size_pt or font_size_pt,
            }
    return {"bold": False, "italic": False, "underline": None, "font_name": font_name, "font_size_pt": font_size_pt}


def _process_remaining(
    remaining_text: str,
    patterns: list[tuple[str, dict]],
    remaining_runs: list[tuple[str, TextRun]],
    default_fmt: dict,
    result: list[TextRun],
    font_name: str,
    font_size_pt: float,
) -> None:
    pos = 0
    current_fmt = default_fmt

    while pos < len(remaining_text):
        best_pos = len(remaining_text)
        best_text = None
        best_fmt: dict | None = None
        best_len = 0

        for pat_text, pat_fmt in patterns:
            found = remaining_text.find(pat_text, pos)
            if found >= pos and (
                found < best_pos or (found == best_pos and len(pat_text) > best_len)
            ):
                best_pos = found
                best_text = pat_text
                best_fmt = pat_fmt
                best_len = len(pat_text)

        if best_text and best_fmt is not None:
            if best_pos > pos:
                _add_run(result, remaining_text[pos:best_pos], **{**current_fmt})
            _add_run(result, best_text, **best_fmt)
            current_fmt = (
                {"bold": False, "italic": False, "underline": None,
                 "font_name": best_fmt.get("font_name", font_name),
                 "font_size_pt": best_fmt.get("font_size_pt", font_size_pt)}
            )
            pos = best_pos + len(best_text)
        else:
            _add_run(result, remaining_text[pos:], **current_fmt)
            break


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_top_padding(
    runs: list[TextRun], text: str, font_name: str, font_size_pt: float
) -> tuple[list[TextRun], str]:
    """Prepend a newline run if text doesn't already start with whitespace."""
    if text and not text[0].isspace():
        pad = TextRun(text="\n", bold=False, italic=False, underline=None,
                      font_name=font_name, font_size_pt=font_size_pt)
        return [pad, *runs], "\n" + text
    return runs, text


def create_runs_from_template(
    text: str,
    template_runs: list[TextRun] | None = None,
    *,
    font_name: str = "Calibri",
    font_size_pt: float = 12.0,
    original_text: str = "",
) -> list[TextRun]:
    """Build a run list for *text* that preserves bold/italic labels from the template.

    Args:
        text: New text to insert (from LLM).
        template_runs: Runs from the original template cell.
        font_name: Fallback font name.
        font_size_pt: Fallback font size.
        original_text: Original cell text (for common-prefix computation).

    Returns:
        List of TextRun objects ready to be written back to the DOCX.
    """
    if not template_runs:
        return [TextRun(text=text, bold=False, italic=False, font_name=font_name, font_size_pt=font_size_pt)]

    result: list[TextRun] = []
    prefix_len = find_common_prefix(text, original_text) if original_text else 0

    if prefix_len > 0:
        result.extend(
            _map_prefix_to_template_runs(text, prefix_len, template_runs, font_name, font_size_pt)
        )

    if len(text) > prefix_len:
        extended = text[prefix_len:]
        prefix_end_idx = _find_prefix_end_run_index(prefix_len, template_runs)

        if prefix_end_idx is not None:
            remaining_runs = _build_remaining_runs(prefix_len, template_runs)
            ext_pos, matched = _try_sequential_matching(
                extended, remaining_runs, result, font_name, font_size_pt
            )

            if ext_pos < len(extended):
                still_remaining = extended[ext_pos:]
                unmatched_runs = remaining_runs[matched:]
                dfmt = _default_format(prefix_end_idx, template_runs, font_name, font_size_pt)
                patterns = _build_search_patterns(unmatched_runs, font_name, font_size_pt)
                _process_remaining(
                    still_remaining, patterns, unmatched_runs, dfmt, result, font_name, font_size_pt
                )
        else:
            _add_run(result, extended, False, False, None, font_name, font_size_pt)
    elif not result:
        _add_run(result, text, False, False, None, font_name, font_size_pt)

    return result
