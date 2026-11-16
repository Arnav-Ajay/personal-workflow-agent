"""Shared HTML building blocks; inline styles keep report content email-friendly.

Only these helpers produce trusted markup. Escape all artifact text at the
point it enters HTML. The shell template adds browser/print enhancements, while
the report body remains usable without JavaScript or an external stylesheet.
"""

from base64 import b64encode
from dataclasses import dataclass, field
from hashlib import sha256
from html import escape
from pathlib import Path
import re
from string import Template

from src.observability import trace

from .artifacts import atomic_write


@dataclass(frozen=True)
class RenderedReport:
    """Rendered HTML and generated PNGs, with a CID form available for email."""

    html: str
    images: dict[str, bytes] = field(default_factory=dict)


def embed_report_images(report: RenderedReport) -> str:
    """Embed trusted generated PNGs for a standalone browser view of email HTML."""
    html = report.html
    for content_id, image in report.images.items():
        html = html.replace(f'src="cid:{content_id}"',
                            f'src="data:image/png;base64,{b64encode(image).decode("ascii")}"')
    return html


@trace("reports.save_images")
def save_report_images(directory: Path, images: dict[str, bytes]) -> dict[str, Path]:
    """Save reusable chart PNGs under content-addressed, safe filenames."""
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", name) for name in images):
        raise ValueError("Chart content IDs must be simple names")
    paths = {}
    for name, content in images.items():
        path = directory / f"{name}-{sha256(content).hexdigest()[:16]}.png"
        atomic_write(path, content)
        paths[name] = path
    return paths


@trace("reports.save_html")
def save_html_report(path: Path, report: str) -> None:
    """Publish a fully rendered HTML file without exposing a partial report."""
    if path.suffix.lower() != ".html":
        raise ValueError("Report output must have an .html extension")
    atomic_write(path, report.encode("utf-8"))


def text(value: object) -> str:
    return escape(str(value), quote=True)


def table(headers: list[str], rows: list[list[str]], empty_message: str) -> str:
    """Build a data table from already escaped/rendered cell markup."""
    if not rows:
        return f'<p style="margin:16px 0;color:#687787;">{text(empty_message)}</p>'
    heading = "".join(
        f'<th scope="col" style="padding:12px 14px;text-align:left;font-size:10px;'
        f'letter-spacing:1px;text-transform:uppercase;color:#687787;border-bottom:1px solid #dce3e9;">{text(header)}</th>'
        for header in headers
    )
    body = "".join("<tr>" + "".join(
        f'<td style="padding:16px 14px;vertical-align:top;border-bottom:1px solid #edf0f3;'
        f'font-size:13px;line-height:1.7;font-variant-numeric:tabular-nums;">{cell}</td>'
        for cell in row
    ) + "</tr>" for row in rows)
    return (f'<div style="overflow-x:auto;" tabindex="0" role="region" aria-label="Report data">'
            f'<table style="width:100%;border-collapse:collapse;" cellpadding="0" cellspacing="0">'
            f'<thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>')


def section(number: str, title: str, subtitle: str, body: str) -> str:
    return (
        '<section style="margin-top:36px;">'
        f'<p style="color:#5472a0;font-size:10px;letter-spacing:2px;font-weight:700;margin:0 0 10px;">{text(number)}</p>'
        f'<h2 style="font-size:23px;letter-spacing:-0.6px;margin:0 0 8px;">{text(title)}</h2>'
        f'<p style="font-size:13px;line-height:1.7;color:#687787;margin:0 0 18px;">{text(subtitle)}</p>'
        f'<div style="background:#fff;border:1px solid #dce3e9;border-radius:12px;padding:8px 12px;">{body}</div>'
        '</section>'
    )


def render_document(*, title: str, body: str, as_of: str, snapshot_id: str, trace_id: str,
                    label: str = "COINBASE REPORT", heading: str = "Your portfolio, at a glance.",
                    description: str = "A clear view of your holdings, orders, and market movement.",
                    notes: list[str] | None = None) -> str:
    template = Template((Path(__file__).parent / "templates/summary.html").read_text(encoding="utf-8"))
    return template.substitute(
        title=text(title), body=body, as_of=text(as_of),
        snapshot_id=text(snapshot_id), trace_id=text(trace_id),
        label=text(label), heading=text(heading), description=text(description),
        notes="".join(f'<p style="margin:0 0 10px;">{text(note)}</p>' for note in notes or []),
    )
