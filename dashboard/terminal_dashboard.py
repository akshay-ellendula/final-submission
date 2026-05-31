import asyncio
import json
import websockets
from collections import deque
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.text import Text
from rich.bar import Bar

API_WS_URL = "ws://localhost:8000/ws/stores"
STORE_ID = "ST1008"

def make_layout() -> Layout:
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="bottom", size=12),
        Layout(name="footer", size=3)
    )
    layout["main"].split_row(
        Layout(name="kpis", ratio=1),
        Layout(name="insights", ratio=1),
    )
    layout["bottom"].split_row(
        Layout(name="funnel", ratio=1),
        Layout(name="anomalies", ratio=1),
    )
    return layout

# Visitor history for sparkline trend (last 30 samples)
_visitor_history: deque = deque(maxlen=30)

def _sparkline(values) -> str:
    """Render a mini sparkline from numeric values using Unicode block chars."""
    if not values:
        return ""
    bars = " ▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    rng = hi - lo or 1
    return "".join(bars[min(8, int((v - lo) / rng * 8))] for v in values)

def render_kpis(metrics) -> Panel:
    if not metrics:
        return Panel("Waiting for stream...", title="Key Performance Indicators")
    
    visitors = metrics.get("unique_visitors", 0)
    _visitor_history.append(visitors)
    
    table = Table(show_header=False, expand=True, border_style="cyan")
    table.add_column("Metric", style="bold white")
    table.add_column("Value", justify="right", style="green")
    
    table.add_row("👥 Unique Visitors", str(visitors))
    table.add_row("📊 Conversion Rate", f"{metrics.get('conversion_rate', 0)*100:.1f}%")
    table.add_row("🛒 POS Transactions", str(metrics.get("pos_transactions", 0)))
    table.add_row("⏳ Queue Depth", str(metrics.get("current_queue_depth", 0)))
    table.add_row("🚪 Abandonment", f"{metrics.get('abandonment_rate', 0)*100:.1f}%")
    table.add_row("👔 Staff Detected", str(metrics.get("staff_count", 0)))
    if len(_visitor_history) > 1:
        spark = _sparkline(list(_visitor_history))
        table.add_row("📉 Trend", Text(spark, style="cyan"))
    
    return Panel(table, title="📈 Key Performance Indicators", border_style="cyan")

def render_insights(metrics) -> Panel:
    if not metrics:
        return Panel("Waiting for stream...", title="Sales Insights")
    
    table = Table(expand=True, border_style="magenta")
    table.add_column("Top Brands", style="bold white")
    table.add_column("Sold", justify="right", style="magenta")
    
    brands = metrics.get("top_brands", {})
    for b, count in brands.items():
        table.add_row(str(b), str(count))
        
    return Panel(table, title="💄 Live Sales Insights", border_style="magenta")

def render_funnel(funnel) -> Panel:
    if not funnel or not funnel.get("stages"):
        return Panel("Waiting for funnel data...", title="Conversion Funnel")
    
    stages = funnel["stages"]
    table = Table(expand=True, border_style="green")
    table.add_column("Stage", style="bold white")
    table.add_column("Count", justify="right", style="green")
    table.add_column("Drop-off", justify="right", style="red")
    table.add_column("Bar", style="cyan", min_width=20)
    
    max_count = max((s["count"] for s in stages), default=1) or 1
    for s in stages:
        bar_width = int((s["count"] / max_count) * 20) if max_count else 0
        bar_str = "█" * bar_width + "░" * (20 - bar_width)
        drop = f"-{s['drop_off_from_prev_pct']:.0f}%" if s["drop_off_from_prev_pct"] > 0 else "—"
        table.add_row(s["stage"], str(s["count"]), drop, bar_str)
    
    conv = funnel.get("conversion_rate", 0)
    return Panel(table, title=f"🔄 Conversion Funnel  (rate: {conv*100:.1f}%)", border_style="green")

# Severity styling: distinct icons + colours for instant visual triage
_SEVERITY_STYLE = {
    "CRITICAL": {"icon": "🔴", "style": "bold red"},
    "WARN":     {"icon": "🟡", "style": "bold yellow"},
    "INFO":     {"icon": "🔵", "style": "bold cyan"},
}

def render_anomalies(anomalies) -> Panel:
    if not anomalies or not anomalies.get("anomalies"):
        return Panel(
            Text("✅ No active anomalies", style="bold green"),
            title="🚨 Anomaly Alerts", border_style="green"
        )
    
    anom_list = anomalies["anomalies"]
    has_critical = any(a.get("severity") == "CRITICAL" for a in anom_list)
    border = "bold red" if has_critical else "yellow"
    
    table = Table(expand=True, border_style=border)
    table.add_column("Sev", style="bold", width=6)
    table.add_column("Type", style="white")
    table.add_column("Action", style="dim white")
    
    for a in anom_list:
        sev = a.get("severity", "INFO")
        cfg = _SEVERITY_STYLE.get(sev, {"icon": "⚪", "style": "white"})
        table.add_row(
            Text(f"{cfg['icon']} {sev}", style=cfg["style"]),
            a.get("type", "UNKNOWN"),
            a.get("suggested_action", "—")[:50],
        )
    
    count = anomalies.get('count', 0)
    return Panel(table, title=f"🚨 Anomaly Alerts ({count})", border_style=border)

async def main():
    console = Console()
    layout = make_layout()
    
    while True:
        try:
            async with websockets.connect(f"{API_WS_URL}/{STORE_ID}") as ws:
                with Live(layout, refresh_per_second=4, screen=True) as live:
                    async for message in ws:
                        data = json.loads(message)
                        m = data.get("metrics")
                        f = data.get("funnel")
                        a = data.get("anomalies")
                        h = data.get("health")
                        
                        # Header
                        status = h.get("status", "DOWN").upper() if h else "DOWN"
                        color = "green" if status == "OK" else "red" if status == "DOWN" else "yellow"
                        header_text = Text(f" 🛍️  Apex Retail Intelligence  |  Store: {STORE_ID}  |  Status: ", style="bold white")
                        header_text.append(status, style=f"bold {color}")
                        layout["header"].update(Panel(header_text, style="blue"))
                        
                        # Main panels
                        layout["kpis"].update(render_kpis(m))
                        layout["insights"].update(render_insights(m))
                        
                        # Bottom panels
                        layout["funnel"].update(render_funnel(f))
                        layout["anomalies"].update(render_anomalies(a))
                        
                        # Footer
                        anom_count = a.get("count", 0) if a else 0
                        visitors = m.get("unique_visitors", 0) if m else 0
                        footer_text = Text(
                            f" Visitors: {visitors}  |  Anomalies: {anom_count}  |  Press Ctrl+C to exit",
                            style="bold white"
                        )
                        layout["footer"].update(Panel(footer_text, style="dim"))
        except (websockets.ConnectionClosed, ConnectionRefusedError):
            print("Connection lost. Reconnecting in 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(3)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
