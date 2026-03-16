"""
Fetch deep YouTube Analytics for OKStorytime using OAuth token.
Pulls: watch time, CTR, impressions, revenue per video.
Saves to okstorytime_analytics.csv
"""

import csv
import json
import os
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

CHANNEL_ID = "UC4Lj84kfpEcwYkfriepZqNQ"


def get_credentials():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    if not creds or not creds.valid:
        raise RuntimeError("No valid token.json found. Run auth_youtube.py first.")
    return creds


def fetch_channel_analytics(youtube_analytics, start_date, end_date):
    """Fetch daily channel analytics, then aggregate by month in Python."""
    resp = youtube_analytics.reports().query(
        ids=f"channel=={CHANNEL_ID}",
        startDate=start_date,
        endDate=end_date,
        metrics="views,estimatedMinutesWatched",
        dimensions="day",
        sort="day"
    ).execute()
    return resp


def fetch_video_analytics(youtube_analytics, start_date, end_date):
    """Fetch per-video analytics."""
    resp = youtube_analytics.reports().query(
        ids=f"channel=={CHANNEL_ID}",
        startDate=start_date,
        endDate=end_date,
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,impressions,impressionClickThroughRate,likes,comments",
        dimensions="video",
        sort="-views",
        maxResults=200
    ).execute()
    return resp


def fetch_video_titles(youtube_data, video_ids):
    """Get titles for video IDs using YouTube Data API."""
    titles = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        resp = youtube_data.videos().list(
            part="snippet,contentDetails",
            id=",".join(batch)
        ).execute()
        for item in resp.get("items", []):
            titles[item["id"]] = {
                "title": item["snippet"].get("title", ""),
                "published_at": item["snippet"].get("publishedAt", "")[:10],
                "duration": item["contentDetails"].get("duration", "")
            }
    return titles


def main():
    print("Authenticating...")
    creds = get_credentials()

    youtube_analytics = build("youtubeAnalytics", "v2", credentials=creds)
    youtube_data      = build("youtube", "v3", credentials=creds)

    today      = datetime.today()
    # Monthly queries need end date = last day of a completed month
    first_of_month = today.replace(day=1)
    last_month_end = first_of_month - timedelta(days=1)
    end_date   = last_month_end.strftime("%Y-%m-%d")
    # Video-level queries can use today
    end_date_daily = today.strftime("%Y-%m-%d")
    start_date = "2022-01-01"

    # ── Monthly channel analytics ────────────────────────────────
    print("Fetching monthly channel analytics...")
    monthly = fetch_channel_analytics(youtube_analytics, start_date, end_date)
    # Aggregate daily rows into monthly buckets
    monthly_map = {}
    if "rows" in monthly:
        headers = [h["name"] for h in monthly["columnHeaders"]]
        for row in monthly["rows"]:
            d = dict(zip(headers, row))
            month_key = d["day"][:7]  # YYYY-MM
            if month_key not in monthly_map:
                monthly_map[month_key] = {"month": month_key, "views": 0, "estimatedMinutesWatched": 0}
            monthly_map[month_key]["views"]                   += int(d.get("views", 0))
            monthly_map[month_key]["estimatedMinutesWatched"] += float(d.get("estimatedMinutesWatched", 0))

    monthly_rows = sorted(monthly_map.values(), key=lambda x: x["month"])

    with open("okstorytime_monthly_analytics.csv", "w", newline="", encoding="utf-8") as f:
        if monthly_rows:
            writer = csv.DictWriter(f, fieldnames=list(monthly_rows[0].keys()))
            writer.writeheader()
            writer.writerows(monthly_rows)
    print(f"  Saved {len(monthly_rows)} months to okstorytime_monthly_analytics.csv")

    # ── Per-video analytics ──────────────────────────────────────
    print("Fetching per-video analytics (top 200 by views)...")
    video_data = fetch_video_analytics(youtube_analytics, start_date, end_date_daily)
    video_rows = []
    if "rows" in video_data:
        headers = [h["name"] for h in video_data["columnHeaders"]]
        for row in video_data["rows"]:
            video_rows.append(dict(zip(headers, row)))

    # Enrich with titles
    if video_rows:
        print("  Fetching video titles...")
        ids = [r["video"] for r in video_rows]
        titles = fetch_video_titles(youtube_data, ids)
        for r in video_rows:
            meta = titles.get(r["video"], {})
            r["title"]        = meta.get("title", "")
            r["published_at"] = meta.get("published_at", "")
            r["duration"]     = meta.get("duration", "")
            # Human-readable CTR
            ctr = r.get("impressionClickThroughRate", 0)
            r["ctr_pct"] = round(float(ctr) * 100, 2) if ctr else 0
            # Avg view duration in minutes
            avd = r.get("averageViewDuration", 0)
            r["avg_view_minutes"] = round(float(avd) / 60, 1) if avd else 0

        with open("okstorytime_video_analytics.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(video_rows[0].keys()))
            writer.writeheader()
            writer.writerows(video_rows)
        print(f"  Saved {len(video_rows)} videos to okstorytime_video_analytics.csv")

    # ── Print summary ────────────────────────────────────────────
    print("\n" + "="*60)
    print("DEEP ANALYTICS SUMMARY")
    print("="*60)

    if monthly_rows:
        total_watchtime = sum(float(r.get("estimatedMinutesWatched", 0)) for r in monthly_rows)
        print(f"Total watch time: {total_watchtime/60:,.0f} hours")

    if video_rows:
        print(f"\nTop 10 videos by views (with CTR & watch time):")
        for i, r in enumerate(video_rows[:10], 1):
            title = r.get("title", r["video"])[:55]
            views = int(r.get("views", 0))
            ctr   = r.get("ctr_pct", 0)
            awt   = r.get("avg_view_minutes", 0)
            print(f"  {i:2}. {views:>8,} views | {ctr:>4.1f}% CTR | {awt:>5.1f}min avg watch | {title}")

    print("\n✅ Done. Files saved:")
    print("   okstorytime_monthly_analytics.csv")
    print("   okstorytime_video_analytics.csv")


if __name__ == "__main__":
    main()
