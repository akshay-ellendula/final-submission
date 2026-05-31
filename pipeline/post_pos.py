"""Post real POS transactions to the API."""
import argparse
import csv
import sys
from pathlib import Path

import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=Path("data-provided/Brigade_Bangalore_10_April_26 (1)bc6219c.csv"))
    p.add_argument("--api-url", default="http://localhost:8000")
    args = p.parse_args()

    if not args.csv.exists():
        print(f"[post_pos] missing {args.csv}", file=sys.stderr)
        return 1

    today_date = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")

    invoices = {}
    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("invoice_type", "").lower() != "sales":
                continue
            
            inv = r.get("invoice_number")
            if not inv:
                continue

            order_time = r.get("order_time", "00:00:00")
            new_ts = f"{today_date}T{order_time}+00:00"

            if inv not in invoices:
                invoices[inv] = {
                    "transaction_id": inv,
                    "store_id": r.get("store_id", "ST1008"),
                    "timestamp": new_ts,
                    "basket_value": 0.0,
                    "items_count": 0,
                    "line_items": [],
                }
            
            try:
                amt = float(r.get("total_amount", 0) or 0)
            except ValueError:
                amt = 0.0
            
            try:
                qty = int(float(r.get("qty", 0) or 0))
            except ValueError:
                qty = 0

            invoices[inv]["basket_value"] += amt
            invoices[inv]["items_count"] += qty
            invoices[inv]["line_items"].append(r)

    rows = list(invoices.values())

    if not rows:
        print("[post_pos] No rows found")
        return 0

    with httpx.Client(timeout=10.0) as c:
        try:
            resp = c.post(f"{args.api_url.rstrip('/')}/pos/ingest", json={"transactions": rows})
            resp.raise_for_status()
            print(f"[post_pos] POSTed {resp.json().get('accepted', 0)} transactions")
        except Exception as e:
            print(f"[post_pos] Error posting: {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

