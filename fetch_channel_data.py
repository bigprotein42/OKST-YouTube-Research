"""
OKStorytime YouTube Channel Data Fetcher
Pulls all public video data using YouTube Data API v3
"""

import requests
import csv
import json
import time
import re
import os
from datetime import datetime

# ============================================================
# API key — set via environment variable or paste here locally
# In GitHub Actions, set YOUTUBE_API_KEY as a repository secret
# ============================================================
API_KEY = os.environ.get("YOUTUBE_API_KEY", "PASTE_YOUR_API_KEY_HERE")
# ============================================================

CHANNEL_ID = "UC4Lj84kfpEcwYkfriepZqNQ"  # okstorytime / @OKOPShow
BASE_URL = "https://www.googleapis.com/youtube/v3"


def get_uploads_playlist_id():
    """Get the uploads playlist ID for the channel."""
    url = f"{BASE_URL}/channels"
    params = {
        "part": "contentDetails,statistics",
        "id": CHANNEL_ID,
        "key": API_KEY
    }
    r = requests.get(url, params=params)
    data = r.json()

    if "error" in data:
        print(f"API Error: {data['error']['message']}")
        return None, None

    channel = data["items"][0]
    playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    stats = channel["statistics"]
    print(f"Channel stats: {stats.get('videoCount')} videos, {stats.get('subscriberCount')} subscribers")
    return playlist_id, stats


def get_all_video_ids(playlist_id):
    """Paginate through uploads playlist to get all video IDs."""
    video_ids = []
    page_token = None
    page = 1

    while True:
        url = f"{BASE_URL}/playlistItems"
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": API_KEY
        }
        if page_token:
            params["pageToken"] = page_token

        r = requests.get(url, params=params)
        data = r.json()

        if "error" in data:
            print(f"API Error: {data['error']['message']}")
            break

        items = data.get("items", [])
        ids = [item["contentDetails"]["videoId"] for item in items]
        video_ids.extend(ids)

        print(f"Page {page}: fetched {len(ids)} videos (total: {len(video_ids)})")
        page += 1

        page_token = data.get("nextPageToken")
        if not page_token:
            break

        time.sleep(0.1)  # be nice to the API

    return video_ids


def parse_duration(duration_str):
    """Convert ISO 8601 duration (PT1H2M3S) to total seconds."""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def get_video_details(video_ids):
    """Fetch details for up to 50 videos at a time."""
    all_videos = []

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        url = f"{BASE_URL}/videos"
        params = {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
            "key": API_KEY
        }
        r = requests.get(url, params=params)
        data = r.json()

        if "error" in data:
            print(f"API Error on batch {i//50 + 1}: {data['error']['message']}")
            continue

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})

            published_at = snippet.get("publishedAt", "")
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00")) if published_at else None

            duration_secs = parse_duration(content.get("duration", "PT0S"))

            video = {
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "published_at": published_at,
                "publish_date": dt.strftime("%Y-%m-%d") if dt else "",
                "publish_day_of_week": dt.strftime("%A") if dt else "",
                "publish_hour": dt.hour if dt else "",
                "publish_year": dt.year if dt else "",
                "publish_month": dt.month if dt else "",
                "duration_seconds": duration_secs,
                "duration_minutes": round(duration_secs / 60, 1),
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "description_snippet": snippet.get("description", "")[:200],
                "tags": "|".join(snippet.get("tags", [])),
                "url": f"https://youtube.com/watch?v={item['id']}"
            }
            all_videos.append(video)

        print(f"  Fetched details for batch {i//50 + 1} ({min(i+50, len(video_ids))}/{len(video_ids)} videos)")
        time.sleep(0.1)

    return all_videos


def save_to_csv(videos, filename="okstorytime_videos.csv"):
    """Save video data to CSV."""
    if not videos:
        print("No videos to save.")
        return

    fieldnames = list(videos[0].keys())
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(videos)
    print(f"\nSaved {len(videos)} videos to {filename}")


def analyze(videos):
    """Run immediate analysis on the fetched data."""
    if not videos:
        return

    print("\n" + "="*60)
    print("OKSTORYTIME CHANNEL ANALYSIS")
    print("="*60)

    # Sort by views
    by_views = sorted(videos, key=lambda x: x["view_count"], reverse=True)

    print(f"\nTotal videos analyzed: {len(videos)}")
    print(f"Total views across all videos: {sum(v['view_count'] for v in videos):,}")
    avg_views = sum(v['view_count'] for v in videos) / len(videos)
    print(f"Average views per video: {avg_views:,.0f}")

    # Top 10 videos
    print("\n--- TOP 10 VIDEOS BY VIEWS ---")
    for i, v in enumerate(by_views[:10], 1):
        print(f"{i}. {v['view_count']:>8,} views | {v['duration_minutes']:>5.1f}min | {v['publish_date']} ({v['publish_day_of_week'][:3]}) | {v['title'][:70]}")

    # Bottom 10 videos (recent only, last 6 months worth)
    recent = [v for v in videos if v.get("publish_year", 0) >= 2024]
    recent_sorted = sorted(recent, key=lambda x: x["view_count"])
    print(f"\n--- LOWEST 10 RECENT VIDEOS (2024+) ---")
    for i, v in enumerate(recent_sorted[:10], 1):
        print(f"{i}. {v['view_count']:>8,} views | {v['duration_minutes']:>5.1f}min | {v['publish_date']} ({v['publish_day_of_week'][:3]}) | {v['title'][:70]}")

    # Performance by day of week
    print("\n--- AVERAGE VIEWS BY DAY OF WEEK ---")
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for day in days:
        day_videos = [v for v in videos if v["publish_day_of_week"] == day]
        if day_videos:
            avg = sum(v["view_count"] for v in day_videos) / len(day_videos)
            count = len(day_videos)
            bar = "█" * int(avg / 500)
            print(f"  {day:<10} | {avg:>8,.0f} avg | {count:>4} videos | {bar}")

    # Performance by video length bucket
    print("\n--- AVERAGE VIEWS BY VIDEO LENGTH ---")
    buckets = [
        ("Under 5 min",   0, 5),
        ("5-15 min",      5, 15),
        ("15-30 min",    15, 30),
        ("30-60 min",    30, 60),
        ("60-90 min",    60, 90),
        ("90-120 min",   90, 120),
        ("Over 120 min", 120, 99999),
    ]
    for label, low, high in buckets:
        bucket_vids = [v for v in videos if low <= v["duration_minutes"] < high]
        if bucket_vids:
            avg = sum(v["view_count"] for v in bucket_vids) / len(bucket_vids)
            print(f"  {label:<15} | {avg:>8,.0f} avg | {len(bucket_vids):>4} videos")

    # Performance by year
    print("\n--- AVERAGE VIEWS BY YEAR (trend check) ---")
    for year in sorted(set(v["publish_year"] for v in videos if v["publish_year"])):
        year_vids = [v for v in videos if v["publish_year"] == year]
        avg = sum(v["view_count"] for v in year_vids) / len(year_vids)
        print(f"  {year} | {avg:>8,.0f} avg | {len(year_vids):>4} videos")

    # Title pattern analysis
    print("\n--- TITLE KEYWORD PERFORMANCE (top keywords in high vs low performers) ---")
    top_25pct = by_views[:len(videos)//4]
    bottom_25pct = by_views[-(len(videos)//4):]

    def get_words(video_list):
        words = {}
        for v in video_list:
            for word in v["title"].lower().split():
                word = re.sub(r'[^a-z]', '', word)
                if len(word) > 3:
                    words[word] = words.get(word, 0) + 1
        return words

    top_words = get_words(top_25pct)
    bottom_words = get_words(bottom_25pct)

    # Words that appear more in top performers
    print("\n  Words MORE common in TOP 25% performing videos:")
    scored = {}
    for word, count in top_words.items():
        if count >= 3:
            bottom_count = bottom_words.get(word, 0.1)
            scored[word] = count / bottom_count
    for word, score in sorted(scored.items(), key=lambda x: -x[1])[:15]:
        print(f"    '{word}' — {score:.1f}x more common in top videos")

    print("\n  Words MORE common in BOTTOM 25% performing videos:")
    scored_bottom = {}
    for word, count in bottom_words.items():
        if count >= 3:
            top_count = top_words.get(word, 0.1)
            scored_bottom[word] = count / top_count
    for word, score in sorted(scored_bottom.items(), key=lambda x: -x[1])[:15]:
        print(f"    '{word}' — {score:.1f}x more common in low videos")

    # Recent trend
    print("\n--- RECENT PERFORMANCE TREND (monthly avg views) ---")
    monthly = {}
    for v in videos:
        if v["publish_year"] >= 2023:
            key = f"{v['publish_year']}-{v['publish_month']:02d}"
            if key not in monthly:
                monthly[key] = []
            monthly[key].append(v["view_count"])
    for month in sorted(monthly.keys()):
        vids = monthly[month]
        avg = sum(vids) / len(vids)
        bar = "█" * int(avg / 300)
        print(f"  {month} | {avg:>7,.0f} avg | {len(vids):>3} videos | {bar}")

    print("\n" + "="*60)
    print("Full data saved to okstorytime_videos.csv")
    print("="*60)


if __name__ == "__main__":
    print("Fetching OKStorytime channel data...\n")

    playlist_id, channel_stats = get_uploads_playlist_id()
    if not playlist_id:
        print("Failed to get channel info. Check your API key.")
        exit()

    print(f"\nFetching all video IDs...")
    video_ids = get_all_video_ids(playlist_id)
    print(f"\nTotal videos found: {len(video_ids)}")

    print(f"\nFetching video details...")
    videos = get_video_details(video_ids)

    save_to_csv(videos)
    analyze(videos)
