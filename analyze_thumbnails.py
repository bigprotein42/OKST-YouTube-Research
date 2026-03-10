"""
Downloads thumbnails for top and bottom performing videos,
then uses Claude vision to analyze what visual patterns work vs don't.
"""

import csv
import os
import requests
import base64
import json
import time
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── Config ───────────────────────────────────────────────────────
ANTHROPIC_API_KEY = "PASTE_YOUR_ANTHROPIC_API_KEY_HERE"
TOP_N = 30    # analyze top N and bottom N videos
THUMB_DIR = "thumbnails"
# ─────────────────────────────────────────────────────────────────

os.makedirs(f"{THUMB_DIR}/top", exist_ok=True)
os.makedirs(f"{THUMB_DIR}/bottom", exist_ok=True)


def load_csv(filename="okstorytime_videos.csv"):
    videos = []
    with open(filename, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["view_count"] = int(row["view_count"] or 0)
            row["duration_minutes"] = float(row["duration_minutes"] or 0)
            videos.append(row)
    return videos


def download_thumbnail(video_id, save_path):
    """Download thumbnail - tries maxres first, falls back to hqdefault."""
    for quality in ["maxresdefault", "hqdefault", "mqdefault"]:
        url = f"https://i.ytimg.com/vi/{video_id}/{quality}.jpg"
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and len(r.content) > 5000:
            with open(save_path, "wb") as f:
                f.write(r.content)
            return True
    return False


def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def analyze_thumbnails_with_claude(top_videos, bottom_videos):
    """Send thumbnails to Claude for visual pattern analysis."""

    # Build content with top thumbnails
    content = [{
        "type": "text",
        "text": f"""You are analyzing YouTube thumbnails for the channel "OK Storytime" — a live comedic relationship advice show where 4 hosts read and react to Reddit stories (r/AITAH, r/relationship_advice, etc).

I'm going to show you {len(top_videos)} TOP performing thumbnails (high views) and {len(bottom_videos)} BOTTOM performing thumbnails (low views).

For each group I'll label them. After seeing all thumbnails, give me:

1. VISUAL PATTERNS IN TOP PERFORMERS:
   - Colors used (background, text, accents)
   - Face expressions (shocked, laughing, disgusted, neutral?)
   - How many faces shown
   - Text overlay style (big/small, caps/lower, emotional words?)
   - Layout (face left/right/center, text placement)
   - Any recurring props, graphics, or design elements

2. VISUAL PATTERNS IN BOTTOM PERFORMERS:
   - Same categories as above
   - What's notably absent vs top performers

3. THE 5 BIGGEST DIFFERENCES between top and bottom thumbnails

4. SPECIFIC ACTIONABLE RECOMMENDATIONS:
   - Exact thumbnail formula to use going forward
   - What to stop doing immediately
   - What emotion/expression drives the most clicks

Be specific and data-driven. Reference what you actually see."""
    }]

    # Add top thumbnails
    content.append({"type": "text", "text": f"\n\n=== TOP {len(top_videos)} PERFORMING THUMBNAILS ==="})
    for i, v in enumerate(top_videos, 1):
        thumb_path = f"{THUMB_DIR}/top/{v['video_id']}.jpg"
        if os.path.exists(thumb_path):
            content.append({"type": "text", "text": f"\nTop #{i} — {v['view_count']:,} views — \"{v['title'][:60]}\""})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_to_base64(thumb_path)}
            })

    # Add bottom thumbnails
    content.append({"type": "text", "text": f"\n\n=== BOTTOM {len(bottom_videos)} PERFORMING THUMBNAILS (recent 2024+) ==="})
    for i, v in enumerate(bottom_videos, 1):
        thumb_path = f"{THUMB_DIR}/bottom/{v['video_id']}.jpg"
        if os.path.exists(thumb_path):
            content.append({"type": "text", "text": f"\nBottom #{i} — {v['view_count']:,} views — \"{v['title'][:60]}\""})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_to_base64(thumb_path)}
            })

    content.append({"type": "text", "text": "\nNow provide your full analysis."})

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-opus-4-6",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": content}]
    }

    print("Sending thumbnails to Claude for analysis...")
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=120)

    if r.status_code != 200:
        print(f"Claude API error: {r.status_code} — {r.text[:300]}")
        return None

    return r.json()["content"][0]["text"]


def main():
    print("Loading video data...")
    videos = load_csv()
    by_views = sorted(videos, key=lambda x: x["view_count"], reverse=True)

    # Top performers (all time)
    top_videos = by_views[:TOP_N]
    # Bottom performers (recent only — 2024+ so they're current format)
    recent = [v for v in videos if int(v.get("publish_year", 0)) >= 2024]
    bottom_videos = sorted(recent, key=lambda x: x["view_count"])[:TOP_N]

    # Download top thumbnails
    print(f"\nDownloading top {TOP_N} thumbnails...")
    top_downloaded = []
    for v in top_videos:
        path = f"{THUMB_DIR}/top/{v['video_id']}.jpg"
        if not os.path.exists(path):
            ok = download_thumbnail(v["video_id"], path)
            if ok:
                print(f"  ✓ {v['title'][:50]}")
            else:
                print(f"  ✗ Failed: {v['video_id']}")
        if os.path.exists(path):
            top_downloaded.append(v)
        time.sleep(0.05)

    # Download bottom thumbnails
    print(f"\nDownloading bottom {TOP_N} recent thumbnails...")
    bottom_downloaded = []
    for v in bottom_videos:
        path = f"{THUMB_DIR}/bottom/{v['video_id']}.jpg"
        if not os.path.exists(path):
            ok = download_thumbnail(v["video_id"], path)
            if ok:
                print(f"  ✓ {v['title'][:50]}")
            else:
                print(f"  ✗ Failed: {v['video_id']}")
        if os.path.exists(path):
            bottom_downloaded.append(v)
        time.sleep(0.05)

    print(f"\nReady: {len(top_downloaded)} top + {len(bottom_downloaded)} bottom thumbnails")

    if ANTHROPIC_API_KEY == "PASTE_YOUR_ANTHROPIC_API_KEY_HERE":
        print("\n⚠️  Add your Anthropic API key to this script to run the AI analysis.")
        print("Get one at: https://console.anthropic.com")
        print("\nThumbnails are downloaded — you can view them in the 'thumbnails' folder.")
        return

    analysis = analyze_thumbnails_with_claude(top_downloaded[:20], bottom_downloaded[:20])

    if analysis:
        print("\n" + "="*65)
        print("THUMBNAIL ANALYSIS")
        print("="*65)
        print(analysis)

        with open("thumbnail_analysis.txt", "w", encoding="utf-8") as f:
            f.write("OKSTORYTIME THUMBNAIL ANALYSIS\n")
            f.write("="*65 + "\n\n")
            f.write(analysis)
        print("\nSaved to thumbnail_analysis.txt")


if __name__ == "__main__":
    main()
