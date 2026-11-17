"""A short navigation page linking independent source reports."""

from urllib.parse import urlsplit

from .reporting import render_document, text


def render_report_index(reports: list[tuple[str, str, str]]) -> str:
    """Link local reports without combining financial data or report bodies."""
    if not reports:
        raise ValueError("No reports available; run the source fetch scripts first")
    cards = []
    for name, href, captured in reports:
        if urlsplit(href).scheme or href.startswith(("//", "\\")):
            raise ValueError("Report index links must be relative local paths")
        cards.append(
            '<tr><td style="padding:24px;border-bottom:1px solid #dce3e9;">'
            f'<a href="{text(href)}" style="font-size:24px;font-weight:700;color:#5279c4;">{text(name)} report</a>'
            f'<p style="font-size:12px;color:#687787;">Saved snapshot: {text(captured)}</p>'
            '</td></tr>'
        )
    body = '<table role="presentation" style="width:100%;background:white;border-collapse:collapse;">' + ''.join(cards) + '</table>'
    return render_document(
        title="Personal workflow | Reports", body=body, as_of="Shown separately in each report",
        snapshot_id="See each report", trace_id="See each report", label="REPORT LIBRARY",
        heading="Choose your report.", description="Coinbase holdings and Numerai performance, in separate reports.",
        notes=["Reports use verified saved snapshots. Refresh the source data before regenerating when you need newer information."],
    )
