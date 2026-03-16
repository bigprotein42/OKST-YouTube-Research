"""
Ingest YouTube Studio CSV exports into unified analytics files.
No OAuth needed — Sam just exports from Studio and drops CSVs here.

Usage:
  1. Put exported CSVs in studio_exports/ folder
  2. Run: python ingest_studio_exports.py
  3. Run: python generate_report.py  (dashboard picks up the data)

Handles the real YouTube Studio Advanced Mode export format:
  - Table data.csv  -> per-video analytics (CTR, impressions, revenue, etc.)
  - Chart data.csv  -> daily per-video engaged views
  - Totals.csv      -> daily channel engaged views
"""

import csv
import os
from collections import defaultdict


EXPORT_DIR = "studio_exports"
VIDEO_OUTPUT = "okstorytime_studio_analytics.csv"
MONTHLY_OUTPUT = "okstorytime_monthly_analytics.csv"


def safe_str(row, key):
    """Safely get a string value from a CSV row (handles None)."""
    val = row.get(key)
    return val.strip() if val else ""


def parse_number(val):
    """Parse numbers that may have commas, %, or $ formatting."""
    if not val:
        return 0
    val = val.strip().strip('"').replace(",", "").replace("%", "").replace("$", "")
    try:
        return float(val)
    except ValueError:
        return 0


def parse_duration_to_minutes(duration_str):
    """Convert 'H:MM:SS' or 'M:SS' to minutes."""
    if not duration_str:
        return 0
    duration_str = duration_str.strip()
    parts = duration_str.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60
        elif len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 60
        else:
            return float(parts[0]) / 60
    except (ValueError, IndexError):
        return 0


def ingest_table_data():
    """Parse 'Table data.csv' — the main per-video analytics export."""
    filepath = os.path.join(EXPORT_DIR, "Table data.csv")
    if not os.path.exists(filepath):
        print("  Table data.csv not found — skipping per-video analytics")
        return []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        videos = []

        for row in reader:
            # Skip the "Total" summary row
            if safe_str(row, "Content") == "Total":
                continue

            video_id = safe_str(row, "Content")
            title = safe_str(row, "Video title")
            if not video_id or not title:
                continue

            duration_sec = parse_number(row.get("Duration"))
            duration_min = round(duration_sec / 60, 1)

            vid = {
                "video_id": video_id,
                "title": title,
                "publish_time": safe_str(row, "Video publish time"),
                "duration_seconds": int(duration_sec),
                "duration_minutes": duration_min,
                "views": int(parse_number(row.get("Views"))),
                "engaged_views": int(parse_number(row.get("Engaged views"))),
                "watch_time_hours": round(parse_number(row.get("Watch time (hours)")), 1),
                "impressions": int(parse_number(row.get("Impressions"))),
                "ctr_pct": round(parse_number(row.get("Impressions click-through rate (%)")), 2),
                "avg_view_duration": safe_str(row, "Average view duration"),
                "avg_view_minutes": round(parse_duration_to_minutes(row.get("Average view duration")), 1),
                "avg_pct_viewed": round(parse_number(row.get("Average percentage viewed (%)")), 1),
                "subscribers_gained": int(parse_number(row.get("Subscribers gained") or row.get("Subscribers"))),
                "subscribers_lost": int(parse_number(row.get("Subscribers lost"))),
                "likes": int(parse_number(row.get("Likes"))),
                "comments": int(parse_number(row.get("Comments added"))),
                "shares": int(parse_number(row.get("Shares"))),
                "estimated_revenue_usd": round(parse_number(row.get("Estimated revenue (USD)")), 2),
                "rpm_usd": round(parse_number(row.get("RPM (USD)")), 2),
                "cpm_usd": round(parse_number(row.get("CPM (USD)")), 2),
                "ad_revenue_usd": round(parse_number(row.get("YouTube ad revenue (USD)")), 2),
                "premium_revenue_usd": round(parse_number(row.get("YouTube Premium (USD)")), 2),
            }
            videos.append(vid)

    print(f"  Table data.csv: {len(videos)} videos loaded")
    return videos


def ingest_chart_data():
    """Parse 'Chart data.csv' — daily per-video engaged views."""
    filepath = os.path.join(EXPORT_DIR, "Chart data.csv")
    if not os.path.exists(filepath):
        print("  Chart data.csv not found — skipping daily per-video data")
        return {}

    # Aggregate to monthly totals per video
    monthly = defaultdict(lambda: defaultdict(int))  # {month: {video_id: engaged_views}}

    count = 0
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = safe_str(row, "Date")
            video_id = safe_str(row, "Content")
            engaged = int(parse_number(row.get("Engaged views")))
            if date and video_id and engaged > 0:
                month = date[:7]  # "2024-01"
                monthly[month][video_id] += engaged
                count += 1

    print(f"  Chart data.csv: {count:,} daily rows -> {len(monthly)} months")
    return dict(monthly)


def ingest_totals():
    """Parse 'Totals.csv' — daily channel engaged views."""
    filepath = os.path.join(EXPORT_DIR, "Totals.csv")
    if not os.path.exists(filepath):
        print("  Totals.csv not found — skipping channel daily totals")
        return {}

    monthly = defaultdict(int)
    count = 0
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = safe_str(row, "Date")
            engaged = int(parse_number(row.get("Engaged views")))
            if date:
                month = date[:7]
                monthly[month] += engaged
                count += 1

    print(f"  Totals.csv: {count:,} daily rows -> {len(monthly)} months")
    return dict(monthly)


def save_video_analytics(data):
    """Save per-video analytics to CSV."""
    if not data:
        print("  No video data to save.")
        return

    fields = [
        "video_id", "title", "publish_time", "duration_seconds", "duration_minutes",
        "views", "engaged_views", "watch_time_hours", "impressions", "ctr_pct",
        "avg_view_duration", "avg_view_minutes", "avg_pct_viewed",
        "subscribers_gained", "subscribers_lost", "likes", "comments", "shares",
        "estimated_revenue_usd", "rpm_usd", "cpm_usd", "ad_revenue_usd", "premium_revenue_usd",
    ]

    with open(VIDEO_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        data.sort(key=lambda x: x.get("views", 0), reverse=True)
        writer.writerows(data)

    print(f"\n  Saved {len(data)} videos -> {VIDEO_OUTPUT}")


def save_monthly_analytics(channel_monthly):
    """Save monthly channel totals to CSV."""
    if not channel_monthly:
        print("  No monthly data to save.")
        return

    fields = ["month", "engaged_views"]
    rows = [{"month": m, "engaged_views": v} for m, v in sorted(channel_monthly.items())]

    with open(MONTHLY_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Saved {len(rows)} months -> {MONTHLY_OUTPUT}")


def print_summary(data):
    """Print a quick summary of the ingested data."""
    if not data:
        return

    total_views = sum(d.get("views", 0) for d in data)
    total_hours = sum(d.get("watch_time_hours", 0) for d in data)
    with_ctr = [d for d in data if d.get("ctr_pct", 0) > 0]
    avg_ctr = sum(d["ctr_pct"] for d in with_ctr) / len(with_ctr) if with_ctr else 0
    total_revenue = sum(d.get("estimated_revenue_usd", 0) for d in data)

    # Separate longform vs shorts
    longform = [d for d in data if d.get("duration_minutes", 0) >= 5]
    shorts = [d for d in data if d.get("duration_minutes", 0) < 1]

    print("\n" + "=" * 60)
    print("STUDIO EXPORT SUMMARY")
    print("=" * 60)
    print(f"  Total videos:   {len(data):,} ({len(longform)} long-form, {len(shorts)} shorts)")
    print(f"  Total views:    {total_views:,}")
    print(f"  Watch time:     {total_hours:,.0f} hours")
    if with_ctr:
        print(f"  Avg CTR:        {avg_ctr:.1f}%")
    if total_revenue > 0:
        print(f"  Total revenue:  ${total_revenue:,.2f}")

    print(f"\n  Top 5 long-form by views:")
    lf_sorted = sorted(longform, key=lambda x: x.get("views", 0), reverse=True)
    for i, d in enumerate(lf_sorted[:5], 1):
        title = d.get("title", "")[:50]
        views = d.get("views", 0)
        ctr = d.get("ctr_pct", 0)
        rev = d.get("estimated_revenue_usd", 0)
        print(f"    {i}. {views:>8,} views | {ctr:>4.1f}% CTR | ${rev:>7.2f} | {title}")


def main():
    print("Ingesting YouTube Studio exports...")
    print(f"  Looking in: {EXPORT_DIR}/\n")

    if not os.path.exists(EXPORT_DIR):
        print(f"  Error: {EXPORT_DIR}/ folder not found.")
        print(f"  Create it and drop your Studio CSV exports there.")
        return

    # Ingest all three export files
    video_data = ingest_table_data()
    chart_monthly = ingest_chart_data()
    channel_monthly = ingest_totals()

    # Save outputs
    save_video_analytics(video_data)
    save_monthly_analytics(channel_monthly)

    # Summary
    print_summary(video_data)

    print(f"\n  Next step: run 'python generate_report.py' to update the dashboard.")


if __name__ == "__main__":
    main()
