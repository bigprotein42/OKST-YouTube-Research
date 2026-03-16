"""
Fetch deep YouTube Analytics for OKStorytime using OAuth token.
Pulls: lifetime analytics + FIRST 24-HOUR CTR per video.
Saves to okstorytime_video_analytics.csv

Scopes: youtube.readonly + yt-analytics.readonly (both READ-ONLY)

Setup:
  1. Run auth_youtube.py once (Sam logs in, generates token.json)
  2. Run this script to fetch all analytics
  3. Run generate_report.py to update the dashboard
"""

import csv
import json
import os
import time
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

CHANNEL_ID = "UC4Lj84kfpEcwYkfriepZqNQ"
FIRST_24H_OUTPUT = "okstorytime_first24h_ctr.csv"


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
    """Fetch per-video lifetime analytics."""
    resp = youtube_analytics.reports().query(
        ids=f"channel=={CHANNEL_ID}",
        startDate=start_date,
        endDate=end_date,
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,impressions,impressionClickThroughRate,likes,comments",
        dimensions="video",
        sort="-views",
        maxResults=500
    ).execute()
    return resp


def fetch_video_titles(youtube_data, video_ids):
    """Get titles + publish dates for video IDs using YouTube Data API v3."""
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


def fetch_first_24h_ctr(youtube_analytics, video_id, publish_date):
    """Fetch impressions + CTR for a single video's first 24 hours.
    We query publish_date to publish_date+1 to capture the first ~24-48h window.
    YouTube Analytics API has a 2-day processing delay, so very recent videos won't have data."""
    try:
        pub = datetime.strptime(publish_date, "%Y-%m-%d")
        end = pub + timedelta(days=1)
        resp = youtube_analytics.reports().query(
            ids=f"channel=={CHANNEL_ID}",
            startDate=publish_date,
            endDate=end.strftime("%Y-%m-%d"),
            metrics="views,impressions,impressionClickThroughRate",
            filters=f"video=={video_id}"
        ).execute()
        if resp.get("rows") and len(resp["rows"]) > 0:
            row = resp["rows"][0]
            headers = [h["name"] for h in resp["columnHeaders"]]
            data = dict(zip(headers, row))
            return {
                "views_24h": int(data.get("views", 0)),
                "impressions_24h": int(data.get("impressions", 0)),
                "ctr_24h_pct": round(float(data.get("impressionClickThroughRate", 0)) * 100, 2),
            }
    except Exception as e:
        print(f"    Warning: Could not fetch 24h CTR for {video_id}: {e}")
    return {"views_24h": 0, "impressions_24h": 0, "ctr_24h_pct": 0}


def load_existing_24h():
    """Load previously fetched first-24h data to avoid re-fetching."""
    existing = {}
    if os.path.exists(FIRST_24H_OUTPUT):
        with open(FIRST_24H_OUTPUT, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                vid = row.get("video_id", "")
                if vid and float(row.get("ctr_24h_pct", 0)) > 0:
                    existing[vid] = row
    return existing


def main():
    print("Authenticating (youtube.readonly + yt-analytics.readonly)...")
    creds = get_credentials()

    youtube_analytics = build("youtubeAnalytics", "v2", credentials=creds)
    youtube_data      = build("youtube", "v3", credentials=creds)

    today      = datetime.today()
    end_date   = today.strftime("%Y-%m-%d")
    start_date = "2021-01-01"

    # ── Monthly channel analytics ────────────────────────────────
    print("Fetching monthly channel analytics...")
    monthly = fetch_channel_analytics(youtube_analytics, start_date, end_date)
    monthly_map = {}
    if "rows" in monthly:
        headers = [h["name"] for h in monthly["columnHeaders"]]
        for row in monthly["rows"]:
            d = dict(zip(headers, row))
            month_key = d["day"][:7]
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

    # ── Per-video lifetime analytics ──────────────────────────────
    print("Fetching per-video analytics (top 500 by views)...")
    video_data = fetch_video_analytics(youtube_analytics, start_date, end_date)
    video_rows = []
    if "rows" in video_data:
        headers = [h["name"] for h in video_data["columnHeaders"]]
        for row in video_data["rows"]:
            video_rows.append(dict(zip(headers, row)))

    # Enrich with titles via Data API v3 (read-only)
    if video_rows:
        print("  Fetching video titles via Data API v3 (read-only)...")
        ids = [r["video"] for r in video_rows]
        titles = fetch_video_titles(youtube_data, ids)
        for r in video_rows:
            meta = titles.get(r["video"], {})
            r["title"]        = meta.get("title", "")
            r["published_at"] = meta.get("published_at", "")
            r["duration"]     = meta.get("duration", "")
            ctr = r.get("impressionClickThroughRate", 0)
            r["ctr_pct"] = round(float(ctr) * 100, 2) if ctr else 0
            avd = r.get("averageViewDuration", 0)
            r["avg_view_minutes"] = round(float(avd) / 60, 1) if avd else 0

        with open("okstorytime_video_analytics.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(video_rows[0].keys()))
            writer.writeheader()
            writer.writerows(video_rows)
        print(f"  Saved {len(video_rows)} videos to okstorytime_video_analytics.csv")

    # ── First 24-Hour CTR per video ──────────────────────────────
    print("\nFetching first-24-hour CTR per video...")
    print("  (This is what Sam looks at in Studio > Reach > First 24 Hours)")

    existing_24h = load_existing_24h()
    print(f"  Already have 24h data for {len(existing_24h)} videos")

    all_24h = []
    to_fetch = []
    for r in video_rows:
        vid = r["video"]
        pub = r.get("published_at", "")
        if not pub:
            continue
        if vid in existing_24h:
            # Re-use cached data
            all_24h.append(existing_24h[vid])
        else:
            to_fetch.append((vid, pub, r.get("title", "")))

    print(f"  Need to fetch 24h CTR for {len(to_fetch)} new videos...")

    for i, (vid, pub, title) in enumerate(to_fetch):
        short_title = title[:50] if title else vid
        print(f"  [{i+1}/{len(to_fetch)}] {short_title}...")
        result = fetch_first_24h_ctr(youtube_analytics, vid, pub)
        all_24h.append({
            "video_id": vid,
            "title": title,
            "published_at": pub,
            "views_24h": result["views_24h"],
            "impressions_24h": result["impressions_24h"],
            "ctr_24h_pct": result["ctr_24h_pct"],
        })
        # Rate limit: YouTube Analytics API allows ~60 requests/min
        if (i + 1) % 50 == 0:
            print("    Pausing 10s for rate limiting...")
            time.sleep(10)
        else:
            time.sleep(0.3)

    # Save all first-24h data
    if all_24h:
        fields = ["video_id", "title", "published_at", "views_24h", "impressions_24h", "ctr_24h_pct"]
        with open(FIRST_24H_OUTPUT, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            all_24h.sort(key=lambda x: float(x.get("ctr_24h_pct", 0)), reverse=True)
            writer.writerows(all_24h)
        print(f"\n  Saved {len(all_24h)} videos to {FIRST_24H_OUTPUT}")

    # ── Print summary ────────────────────────────────────────────
    print("\n" + "="*60)
    print("ANALYTICS SUMMARY")
    print("="*60)

    if monthly_rows:
        total_watchtime = sum(float(r.get("estimatedMinutesWatched", 0)) for r in monthly_rows)
        print(f"Total watch time: {total_watchtime/60:,.0f} hours")

    if video_rows:
        print(f"\nTop 10 videos by LIFETIME views (with lifetime CTR):")
        for i, r in enumerate(video_rows[:10], 1):
            title = r.get("title", r["video"])[:50]
            views = int(r.get("views", 0))
            ctr   = r.get("ctr_pct", 0)
            awt   = r.get("avg_view_minutes", 0)
            print(f"  {i:2}. {views:>8,} views | {ctr:>5.1f}% CTR | {awt:>5.1f}min avg | {title}")

    if all_24h:
        top_24h = sorted(all_24h, key=lambda x: float(x.get("ctr_24h_pct", 0)), reverse=True)
        with_data = [v for v in top_24h if float(v.get("ctr_24h_pct", 0)) > 0]
        print(f"\nTop 10 videos by FIRST 24-HOUR CTR (the metric Sam watches):")
        for i, r in enumerate(with_data[:10], 1):
            title = r.get("title", r["video_id"])[:50]
            ctr24 = float(r.get("ctr_24h_pct", 0))
            views24 = int(r.get("views_24h", 0))
            impr24 = int(r.get("impressions_24h", 0))
            print(f"  {i:2}. {ctr24:>5.1f}% CTR | {views24:>6,} views | {impr24:>7,} impr | {title}")

    print("\nFiles saved:")
    print("   okstorytime_monthly_analytics.csv")
    print("   okstorytime_video_analytics.csv")
    print(f"   {FIRST_24H_OUTPUT}  <-- THIS IS THE KEY ONE")
    print("\nNext: run generate_report.py to update the dashboard.")


if __name__ == "__main__":
    main()
