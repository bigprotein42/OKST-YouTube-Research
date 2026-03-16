"""
Analyze the first 30-60 seconds of top-performing vs average OKStorytime videos.
Fetches transcripts via youtube_transcript_api and compares intro patterns.
"""

import csv
import re
import sys
import time
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)

CSV_PATH = r"C:\Users\Riley\Desktop\Claude Code\YouTube Research\okstorytime_videos.csv"

# ── 1. Load CSV ──────────────────────────────────────────────────────────────

def load_videos(path):
    videos = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["view_count"] = int(row["view_count"])
                row["duration_seconds"] = int(row["duration_seconds"])
                row["duration_minutes"] = float(row["duration_minutes"])
            except (ValueError, KeyError):
                continue
            videos.append(row)
    return videos


def is_livestream(title):
    t = title.lower()
    if "\U0001f534" in title:  # 🔴
        return True
    if re.search(r"\blive\b", t) or re.search(r"\bstream\b", t) or re.search(r"\blivestream\b", t):
        return True
    return False


# ── 2. Select top-10 and median-5 long-form videos ──────────────────────────

def select_videos(videos):
    # Long-form only: >= 300 seconds (5 min), no livestreams
    longform = [
        v for v in videos
        if v["duration_seconds"] >= 300 and not is_livestream(v["title"])
    ]

    # Sort by views descending
    longform.sort(key=lambda v: v["view_count"], reverse=True)

    top10 = longform[:10]

    # Find median range
    n = len(longform)
    mid = n // 2
    # Take 5 videos around the median
    median5 = longform[mid - 2 : mid + 3]

    return top10, median5


# ── 3. Fetch transcript and extract intro text ──────────────────────────────

def fetch_intro(video_id, max_seconds=60):
    """Return (text_0_30, text_0_60) or (None, None) on failure."""
    try:
        api = YouTubeTranscriptApi()
        entries = api.fetch(video_id, languages=["en"])
    except Exception as e:
        return None, None, str(e)

    text_30 = []
    text_60 = []

    for entry in entries:
        # Handle both FetchedTranscriptSnippet objects and dicts
        if hasattr(entry, "start"):
            start = entry.start
            txt = entry.text
        elif isinstance(entry, dict):
            start = entry.get("start", 0)
            txt = entry.get("text", "")
        else:
            continue

        if start <= 30:
            text_30.append(txt)
        if start <= 60:
            text_60.append(txt)

    return " ".join(text_30), " ".join(text_60), None


# ── 4. Analyze and print ────────────────────────────────────────────────────

def print_video_intro(v, idx, label):
    vid = v["video_id"]
    title = v["title"]
    views = v["view_count"]
    dur = v["duration_minutes"]

    print(f"\n{'='*90}")
    print(f"  {label} #{idx+1}: {title}")
    print(f"  Views: {views:,}  |  Duration: {dur:.1f} min  |  ID: {vid}")
    print(f"{'='*90}")

    t30, t60, err = fetch_intro(vid)
    if err:
        print(f"  [Transcript error: {err}]")
        return None, None

    print(f"\n  --- FIRST 30 SECONDS ---")
    print(f"  {t30}")
    print(f"\n  --- FIRST 60 SECONDS ---")
    print(f"  {t60}")

    return t30, t60


def main():
    print("Loading CSV...")
    videos = load_videos(CSV_PATH)
    print(f"  Loaded {len(videos)} videos.")

    top10, median5 = select_videos(videos)

    print(f"\n{'#'*90}")
    print(f"  TOP 10 VIDEOS BY VIEWS (long-form, no livestreams)")
    print(f"{'#'*90}")

    top_intros = []
    for i, v in enumerate(top10):
        t30, t60 = print_video_intro(v, i, "TOP")[:2]
        top_intros.append({"title": v["title"], "views": v["view_count"], "t30": t30, "t60": t60})
        time.sleep(0.5)  # be polite to API

    print(f"\n\n{'#'*90}")
    print(f"  5 MEDIAN-PERFORMING VIDEOS (for comparison)")
    print(f"{'#'*90}")

    med_intros = []
    for i, v in enumerate(median5):
        t30, t60 = print_video_intro(v, i, "MEDIAN")[:2]
        med_intros.append({"title": v["title"], "views": v["view_count"], "t30": t30, "t60": t60})
        time.sleep(0.5)

    # ── Summary stats ────────────────────────────────────────────────────
    print(f"\n\n{'#'*90}")
    print(f"  QUICK STATS")
    print(f"{'#'*90}")
    top_views = [v["views"] for v in top_intros]
    med_views = [v["views"] for v in med_intros]
    print(f"  Top 10 view range : {min(top_views):,} - {max(top_views):,}")
    print(f"  Median 5 view range: {min(med_views):,} - {max(med_views):,}")

    # Word-count comparison
    top_wc30 = [len(v["t30"].split()) for v in top_intros if v["t30"]]
    med_wc30 = [len(v["t30"].split()) for v in med_intros if v["t30"]]
    top_wc60 = [len(v["t60"].split()) for v in top_intros if v["t60"]]
    med_wc60 = [len(v["t60"].split()) for v in med_intros if v["t60"]]

    if top_wc30:
        print(f"  Avg words in first 30s — Top: {sum(top_wc30)/len(top_wc30):.0f}, Median: {sum(med_wc30)/len(med_wc30):.0f}")
    if top_wc60:
        print(f"  Avg words in first 60s — Top: {sum(top_wc60)/len(top_wc60):.0f}, Median: {sum(med_wc60)/len(med_wc60):.0f}")


if __name__ == "__main__":
    main()
