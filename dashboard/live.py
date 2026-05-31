"""Live terminal dashboard — polls /metrics, /anomalies, /funnel every 2s.

Usage:
    python -m dashboard.live --store STORE_001 --api http://localhost:8000
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


SEVERITY_STYLE = {
    "CRITICAL": "bold white on red",
    "WARN": "bold black on yellow",
    "INFO": "cyan",
}


def fetch(client: httpx.Client, url: str) -> dict[str, Any] | None:
    try:
        r = client.get(url, timeout=5.0)
        if r.status_code == 200:
            return r.json()
        return {"_error": f"HTTP {r.status_code}"}
    except httpx.HTTPError as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def render(store_id: str, metrics: dict, funnel: dict, anomalies: dict, health: dict) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=6),
    )
    layout["body"].split_row(
        Layout(name="metrics"),
        Layout(name="funnel"),
    )

    # ---------------- header -------------------------------------------------
    hdr = Table.grid(expand=True)
    hdr.add_column(justify="left")
    hdr.add_column(justify="right")
    api_status = health.get("status", "?") if isinstance(health, dict) else "?"
    hdr.add_row(
        Text(f"Apex Retail — {store_id}", style="bold magenta"),
        Text(f"API status: {api_status}    {time.strftime('%H:%M:%S')}", style="dim"),
    )
    layout["header"].update(Panel(hdr, border_style="magenta"))

    # ---------------- metrics panel ------------------------------------------
    mt = Table(title="Today's metrics", expand=True, show_header=True, header_style="bold cyan")
    mt.add_column("Metric")
    mt.add_column("Value", justify="right")
    if metrics and "_error" not in metrics:
        mt.add_row("Unique visitors", str(metrics.get("unique_visitors", 0)))
        mt.add_row("Conversion rate", f"{(metrics.get('conversion_rate', 0) * 100):.2f}%")
        mt.add_row("Abandonment rate", f"{(metrics.get('abandonment_rate', 0) * 100):.2f}%")
        mt.add_row("Current queue depth", str(metrics.get("current_queue_depth", 0)))
        mt.add_row("POS transactions", str(metrics.get("pos_transactions", 0)))
        for zone, ms in (metrics.get("avg_dwell_per_zone_ms") or {}).items():
            mt.add_row(f"  dwell / {zone}", f"{ms/1000:.1f}s")
    else:
        mt.add_row("(no data)", "")
    layout["metrics"].update(Panel(mt, border_style="cyan"))

    # ---------------- funnel panel -------------------------------------------
    ft = Table(title="Conversion funnel", expand=True, show_header=True, header_style="bold green")
    ft.add_column("Stage")
    ft.add_column("Count", justify="right")
    ft.add_column("Drop-off", justify="right")
    if funnel and "_error" not in funnel:
        for s in funnel.get("stages", []):
            ft.add_row(s["stage"], str(s["count"]), f"{s['drop_off_from_prev_pct']:.1f}%")
    else:
        ft.add_row("(no data)", "", "")
    layout["funnel"].update(Panel(ft, border_style="green"))

    # ---------------- anomalies footer ---------------------------------------
    at = Table(title="Active anomalies", expand=True, show_header=True)
    at.add_column("Type")
    at.add_column("Severity")
    at.add_column("Action")
    if anomalies and "_error" not in anomalies and anomalies.get("anomalies"):
        for a in anomalies["anomalies"][:5]:
            sev = a.get("severity", "INFO")
            at.add_row(
                a.get("type", "?"),
                Text(sev, style=SEVERITY_STYLE.get(sev, "")),
                a.get("suggested_action", ""),
            )
    else:
        at.add_row("(none)", "", "")
    layout["footer"].update(Panel(at, border_style="yellow"))
    return layout


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--store", default="STORE_001")
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--interval", type=float, default=2.0)
    args = p.parse_args(argv)

    console = Console()
    with httpx.Client() as client, Live(
        Panel(Text("connecting...", style="dim"), title="Apex"),
        console=console,
        refresh_per_second=4,
        screen=False,
    ) as live:
        while True:
            metrics = fetch(client, f"{args.api}/stores/{args.store}/metrics") or {}
            funnel = fetch(client, f"{args.api}/stores/{args.store}/funnel") or {}
            anomalies = fetch(client, f"{args.api}/stores/{args.store}/anomalies") or {}
            health = fetch(client, f"{args.api}/health") or {}
            live.update(render(args.store, metrics, funnel, anomalies, health))
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
