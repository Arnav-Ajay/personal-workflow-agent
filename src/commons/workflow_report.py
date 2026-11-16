"""Compose source-specific report sections into a collective HTML/email view."""

from datetime import datetime, timezone
from typing import Any

from src.coinbase.reporting import REPORT_NOTE as COINBASE_NOTE
from src.coinbase.reporting import render_coinbase_body
from src.numerai.reporting import REPORT_NOTE as NUMERAI_NOTE
from src.numerai.reporting import build_numerai_charts, render_numerai_body
from src.observability import trace

from .reporting import RenderedReport, embed_report_images, render_document, text


def _source_heading(name: str, snapshot: dict[str, Any]) -> str:
    captured = datetime.fromisoformat(snapshot["as_of"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return (
        '<table role="presentation" class="source-heading" style="width:100%;border-collapse:collapse;'
        'margin:44px 0 24px;border-top:3px solid #5279c4;"><tr>'
        f'<td style="padding:18px 0;font-size:26px;font-weight:700;">{text(name)}</td>'
        f'<td style="text-align:right;padding:18px 0;font-size:11px;color:#687787;">'
        f'Saved snapshot<br>{text(captured.strftime("%d %b %Y, %H:%M UTC"))}</td></tr></table>'
    )


def _missing(name: str, script: str) -> str:
    return (f'<div style="margin:30px 0;padding:22px;border:1px dashed #bdc8d5;border-radius:12px;">'
            f'<h2 style="font-size:20px;margin:0 0 10px;">{text(name)}</h2>'
            f'<p style="color:#687787;font-size:13px;">No saved snapshot available. Run '
            f'<code>python scripts/{text(script)}</code>, then regenerate this report.</p></div>')


@trace("workflow.render_report")
def render_workflow_report(coinbase: dict[str, Any] | None, numerai: dict[str, Any] | None, *,
                           email: bool = False) -> RenderedReport:
    """Return reusable HTML plus PNGs; email=True keeps cid: attachment references.

    The browser view embeds images and needs no server or JavaScript. A future
    email sender can attach images with Content-ID equal to their mapping keys.
    """
    if coinbase is None and numerai is None:
        raise ValueError("No Coinbase or Numerai snapshots available; run the fetch scripts first")
    for name, snapshot in (("coinbase", coinbase), ("numerai", numerai)):
        if snapshot is not None and (not isinstance(snapshot, dict) or snapshot.get("source") != name):
            raise ValueError(f"Expected a {name} snapshot")
    images = {}
    notes, identities, traces = [], [], []
    body = ('<p style="padding:14px 18px;background:#e9eef7;border-radius:9px;font-size:12px;'
            'line-height:1.8;color:#344760;">Saved-source summary. Coinbase and Numerai may have '
            'different capture times, shown below. USDC holdings and NMR performance are reported separately.</p>')
    if coinbase is not None:
        body += _source_heading("Coinbase · Holdings & activity", coinbase) + render_coinbase_body(coinbase)
        notes.append(COINBASE_NOTE)
    else:
        body += _missing("Coinbase", "coinbase_account_summary.py")
    if numerai is not None:
        images = build_numerai_charts(numerai["data"])
        body += _source_heading("Numerai · Account & model", numerai) + render_numerai_body(numerai, images=images)
        notes.append(NUMERAI_NOTE)
    else:
        body += _missing("Numerai", "numerai_account_summary.py")
    for name, snapshot in (("Coinbase", coinbase), ("Numerai", numerai)):
        if snapshot is not None:
            identities.append(f"{name}: {snapshot['snapshot_id']}")
            traces.append(f"{name}: {snapshot.get('trace_id') or 'Not recorded'}")
    html = render_document(
        title="Personal workflow · Coinbase & Numerai", body=body,
        as_of="Independent source timestamps shown in each section",
        snapshot_id=" | ".join(identities), trace_id=" | ".join(traces),
        label="WORKFLOW SUMMARY", heading="Your workflow, at a glance.",
        description="Coinbase holdings, Numerai performance, and the rounds to watch.", notes=notes,
    )
    report = RenderedReport(html, images)
    return report if email else RenderedReport(embed_report_images(report), images)
