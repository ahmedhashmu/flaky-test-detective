"""Output formats.

Formatting only. If a reporter needs a derived number it belongs in `analysis`,
otherwise the console, Markdown, JSON, and HTML outputs will disagree with each
other over time.

Adding a format means adding a module here and registering it below.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import AnalysisReport, TriageReport
from . import console, html, json_report, markdown

FORMATS: dict[str, Callable[[AnalysisReport], str]] = {
    "md": markdown.render_report,
    "markdown": markdown.render_report,
    "json": json_report.render_report,
    "html": html.render_report,
}

TRIAGE_FORMATS: dict[str, Callable[[TriageReport], str]] = {
    "md": markdown.render_triage,
    "markdown": markdown.render_triage,
    "json": json_report.render_triage,
}

TEXT_FORMATS = ("console", "md", "markdown", "json", "html")


def render(report: AnalysisReport, fmt: str) -> str:
    """Render to a string in the named format.

    `console` is excluded here because it writes directly to a terminal rather
    than returning text.
    """
    try:
        return FORMATS[fmt](report)
    except KeyError:
        raise ValueError(
            f"Unknown format {fmt!r}. Available: {', '.join(sorted(FORMATS))}"
        ) from None


def render_triage(result: TriageReport, fmt: str) -> str:
    try:
        return TRIAGE_FORMATS[fmt](result)
    except KeyError:
        raise ValueError(
            f"Unknown triage format {fmt!r}. Available: {', '.join(sorted(TRIAGE_FORMATS))}"
        ) from None


__all__ = [
    "FORMATS",
    "TEXT_FORMATS",
    "TRIAGE_FORMATS",
    "console",
    "html",
    "json_report",
    "markdown",
    "render",
    "render_triage",
]
