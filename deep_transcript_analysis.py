"""
Deep transcript analysis of OKStorytime videos.
Compares top-50 vs bottom-50 long-form videos by analyzing first 30/60 seconds.
Uses yt-dlp with proxy + node JS runtime to fetch auto-generated captions.
"""

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
from collections import Counter
from html import unescape

# ── Config ──────────────────────────────────────────────────────────────────
CSV_PATH = r"C:\Users\Riley\Desktop\Claude Code\YouTube Research\okstorytime_videos.csv"
OUTPUT_PATH = r"C:\Users\Riley\Desktop\Claude Code\YouTube Research\transcript_analysis.json"

# Free HTTP proxies (rotated on failure)
PROXIES = [
    "45.88.0.116:3128",
    "185.130.225.170:8080",
    "101.47.73.135:3128",
]

# Topic keywords to detect
TOPIC_KEYWORDS = {
    "cheating": r"\b(cheat|cheating|cheated|affair|unfaithful|infidel)\b",
    "wedding": r"\b(wedding|bride|groom|marry|married|marriage|engagement|engaged|fianc)\b",
    "family": r"\b(family|parent|mother|father|mom|dad|sister|brother|sibling|aunt|uncle|grandma|grandpa|grandmother|grandfather|in-law|stepmother|stepfather|stepmom|stepdad)\b",
    "divorce": r"\b(divorce|divorced|custody|separated|separation)\b",
    "revenge": r"\b(revenge|payback|karma|got back at|taught.*lesson)\b",
    "aita": r"\b(aita|am i the|ahole|a-hole)\b",
    "entitled": r"\b(entitled|karen|choosing beggar)\b",
    "neighbor": r"\b(neighbor|neighbourhood|HOA)\b",
    "workplace": r"\b(boss|coworker|co-worker|fired|job|workplace|manager|HR|quit|resign)\b",
    "inheritance": r"\b(inherit|inheritance|will|estate|trust fund)\b",
    "relationship": r"\b(boyfriend|girlfriend|partner|dating|breakup|broke up|ex-|toxic)\b",
    "children": r"\b(child|children|kid|baby|son|daughter|pregnant|pregnancy|custody)\b",
    "roommate": r"\b(roommate|room mate|flatmate|housemate)\b",
    "update": r"\b(update|part 2|follow.?up|continuation)\b",
    "nuclear_revenge": r"\b(nuclear|pro.?revenge|malicious.?compliance)\b",
    "money": r"\b(money|debt|loan|owe|pay|financial|rent|mortgage)\b",
    "school": r"\b(school|teacher|student|bully|bullied|college|university|professor)\b",
}

# Channel plug patterns
CHANNEL_PLUG_PATTERNS = [
    r"\b(subscribe|like and subscribe|hit the bell|notification)\b",
    r"\b(comment below|let me know|leave a comment)\b",
    r"\b(check out|link in|description below)\b",
    r"\b(welcome back|welcome to)\b",
    r"\b(patreon|members|join)\b",
    r"\b(discord|community)\b",
]

# Host detection patterns
HOST_PATTERNS = {
    "Sam": [r"\bsam\b", r"\bsamuel\b", r"\bi'm sam\b"],
    "John": [r"\bjohn\b", r"\bi'm john\b"],
}


def load_videos(path):
    """Load and parse the video CSV."""
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
    if "\U0001f534" in title:  # red circle emoji
        return True
    return bool(re.search(r"\b(stream|live|livestream|vod)\b", t, re.IGNORECASE))


def select_top_bottom(videos):
    """Select top 50 overall and bottom 50 from 2024+."""
    longform = [
        v for v in videos
        if v["duration_minutes"] >= 5 and not is_livestream(v["title"])
    ]
    longform.sort(key=lambda v: v["view_count"], reverse=True)

    top_50 = longform[:50]

    # Bottom 50 from 2024+ only
    recent = [v for v in longform if v["publish_date"] >= "2024-01-01"]
    recent.sort(key=lambda v: v["view_count"])
    bottom_50 = recent[:50]

    return top_50, bottom_50


def fetch_transcript_yt_dlp(video_id, proxy=None):
    """Fetch transcript via yt-dlp. Returns list of {start, text} or None."""
    tmpdir = tempfile.mkdtemp()
    outpath = os.path.join(tmpdir, "%(id)s.%(ext)s")

    cmd = ["yt-dlp"]
    if proxy:
        cmd += ["--proxy", f"http://{proxy}"]
    cmd += [
        "--js-runtimes", "node",
        "--skip-download",
        "--write-auto-sub",
        "--sub-lang", "en",
        "--sub-format", "json3",
        "--no-warnings",
        "-o", outpath,
        f"https://www.youtube.com/watch?v={video_id}",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=45
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, str(e)

    # Find the subtitle file
    sub_file = os.path.join(tmpdir, f"{video_id}.en.json3")
    if not os.path.exists(sub_file):
        err = result.stderr.strip() if result.stderr else "no subtitle file"
        return None, err[:200]

    try:
        with open(sub_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"parse error: {e}"

    events = data.get("events", [])
    segments = []
    for ev in events:
        start_ms = ev.get("tStartMs", 0)
        segs = ev.get("segs", [])
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if text and text != "\n":
            segments.append({"start": start_ms / 1000.0, "text": text})

    # Clean up
    try:
        os.remove(sub_file)
        os.rmdir(tmpdir)
    except OSError:
        pass

    if not segments:
        return None, "empty transcript"

    return segments, None


def extract_intro_text(segments, max_seconds):
    """Extract text from segments up to max_seconds."""
    return " ".join(
        s["text"] for s in segments if s["start"] < max_seconds
    ).strip()


def classify_opening(text_30):
    """Classify the opening style."""
    if not text_30:
        return "unknown"
    t = text_30.lower()

    # Cold open: starts directly with story content, no host intro
    if re.match(r"^(so|my|i |we |he |she |they |the |this |okay so|alright so)", t):
        return "cold_open"
    if re.match(r"^(what|how|why|where|who|when|would|could|should|is it|am i|do i)", t):
        return "question"
    if re.search(r"^(hey |hi |hello|welcome|what's up|good morning|good evening)", t):
        return "host_intro"
    if re.search(r"^(today|in this|this story|this video|this one|here's)", t):
        return "story_summary"
    if re.search(r"^(>>|\")", t):
        return "cold_open"  # Direct quote/story text

    return "other"


def detect_host(text_60):
    """Try to detect the host from transcript text."""
    if not text_60:
        return "Unknown"
    t = text_60.lower()

    for host, patterns in HOST_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, t, re.IGNORECASE):
                return host
    return "Unknown"


def detect_topics(title, text_60):
    """Detect topics from title and first 60s of transcript."""
    combined = (title + " " + (text_60 or "")).lower()
    found = []
    for topic, pattern in TOPIC_KEYWORDS.items():
        if re.search(pattern, combined, re.IGNORECASE):
            found.append(topic)
    return found if found else ["general"]


def has_channel_plug(text_60):
    """Check if first 60s contains channel promotion."""
    if not text_60:
        return False
    t = text_60.lower()
    for pattern in CHANNEL_PLUG_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    return False


def classify_title_pattern(title):
    """Classify title structure."""
    patterns = []
    t = title.lower()

    if "..." in title or "\u2026" in title:
        patterns.append("ellipsis")
    if "!" in title:
        patterns.append("exclamation")
    if "?" in title:
        patterns.append("question")
    if "|" in title:
        patterns.append("pipe_separator")
    if re.search(r"reddit\s*stor", t):
        patterns.append("reddit_stories_tag")
    if re.search(r"\bpart\s*\d", t):
        patterns.append("multi_part")
    if re.search(r"\bupdate\b", t):
        patterns.append("update")
    if re.search(r"(compilation|best of|top \d)", t):
        patterns.append("compilation")
    if re.search(r'^"', title) or re.search(r"^'", title):
        patterns.append("starts_with_quote")
    if len(title) > 80:
        patterns.append("long_title")
    elif len(title) < 40:
        patterns.append("short_title")
    if re.search(r"\ball caps\b", t) or title == title.upper() and len(title) > 10:
        patterns.append("all_caps")
    if any(word in t for word in ["shocking", "insane", "unbelievable", "worst", "craziest", "wild"]):
        patterns.append("sensational")
    if any(word in t for word in ["exposed", "caught", "confronted", "snapped"]):
        patterns.append("dramatic_verb")

    return patterns if patterns else ["plain"]


def analyze_results(top_data, bottom_data):
    """Generate comparative analysis."""
    analysis = {}

    # ── Opening patterns ──
    top_openings = Counter(v["opening_style"] for v in top_data)
    bot_openings = Counter(v["opening_style"] for v in bottom_data)
    analysis["opening_patterns"] = {
        "top": dict(top_openings.most_common()),
        "bottom": dict(bot_openings.most_common()),
        "insight": (
            f"Top videos: most common opening is '{top_openings.most_common(1)[0][0]}' "
            f"({top_openings.most_common(1)[0][1]}/{len(top_data)}). "
            f"Bottom videos: most common is '{bot_openings.most_common(1)[0][0]}' "
            f"({bot_openings.most_common(1)[0][1]}/{len(bottom_data)})."
        ),
    }

    # ── Host correlation ──
    top_hosts = Counter(v["detected_host"] for v in top_data)
    bot_hosts = Counter(v["detected_host"] for v in bottom_data)
    analysis["host_correlation"] = {
        "top": dict(top_hosts.most_common()),
        "bottom": dict(bot_hosts.most_common()),
        "insight": (
            "Host detection from auto-captions is limited. "
            f"Top videos hosts: {dict(top_hosts)}. "
            f"Bottom videos hosts: {dict(bot_hosts)}."
        ),
    }

    # ── Topic correlation ──
    top_topics = Counter()
    bot_topics = Counter()
    for v in top_data:
        top_topics.update(v["topics"])
    for v in bottom_data:
        bot_topics.update(v["topics"])

    # Find topics that are disproportionately in top vs bottom
    all_topics = set(top_topics.keys()) | set(bot_topics.keys())
    topic_ratios = {}
    for topic in all_topics:
        t = top_topics.get(topic, 0) / max(len(top_data), 1)
        b = bot_topics.get(topic, 0) / max(len(bottom_data), 1)
        topic_ratios[topic] = {"top_pct": round(t * 100, 1), "bottom_pct": round(b * 100, 1)}

    # Sort by difference (top - bottom)
    sorted_topics = sorted(
        topic_ratios.items(), key=lambda x: x[1]["top_pct"] - x[1]["bottom_pct"], reverse=True
    )

    analysis["topic_correlation"] = {
        "top_10_topics": dict(top_topics.most_common(10)),
        "bottom_10_topics": dict(bot_topics.most_common(10)),
        "topics_favoring_top": {k: v for k, v in sorted_topics[:5]},
        "topics_favoring_bottom": {k: v for k, v in sorted_topics[-5:]},
    }

    # ── Pacing differences ──
    top_plug = sum(1 for v in top_data if v["has_channel_plug_in_first_60s"])
    bot_plug = sum(1 for v in bottom_data if v["has_channel_plug_in_first_60s"])

    top_wc30 = [len(v["first_30s"].split()) for v in top_data if v["first_30s"]]
    bot_wc30 = [len(v["first_30s"].split()) for v in bottom_data if v["first_30s"]]

    analysis["pacing_differences"] = {
        "channel_plug_in_first_60s": {
            "top": f"{top_plug}/{len(top_data)}",
            "bottom": f"{bot_plug}/{len(bottom_data)}",
        },
        "avg_words_first_30s": {
            "top": round(sum(top_wc30) / max(len(top_wc30), 1), 1),
            "bottom": round(sum(bot_wc30) / max(len(bot_wc30), 1), 1),
        },
        "insight": (
            f"Top videos have channel plug in first 60s: {top_plug}/{len(top_data)}. "
            f"Bottom: {bot_plug}/{len(bottom_data)}. "
            f"Avg words in first 30s - Top: {round(sum(top_wc30)/max(len(top_wc30),1),1)}, "
            f"Bottom: {round(sum(bot_wc30)/max(len(bot_wc30),1),1)}."
        ),
    }

    # ── Title patterns ──
    top_title_patterns = Counter()
    bot_title_patterns = Counter()
    for v in top_data:
        top_title_patterns.update(v.get("title_patterns", []))
    for v in bottom_data:
        bot_title_patterns.update(v.get("title_patterns", []))

    analysis["title_patterns"] = {
        "top": dict(top_title_patterns.most_common(10)),
        "bottom": dict(bot_title_patterns.most_common(10)),
        "avg_title_length": {
            "top": round(sum(len(v["title"]) for v in top_data) / max(len(top_data), 1), 1),
            "bottom": round(sum(len(v["title"]) for v in bottom_data) / max(len(bottom_data), 1), 1),
        },
    }

    # ── Content structure ──
    top_durations = [v["duration_min"] for v in top_data]
    bot_durations = [v["duration_min"] for v in bottom_data]

    analysis["content_structure"] = {
        "avg_duration_min": {
            "top": round(sum(top_durations) / max(len(top_durations), 1), 1),
            "bottom": round(sum(bot_durations) / max(len(bot_durations), 1), 1),
        },
        "compilation_count": {
            "top": sum(1 for v in top_data if "compilation" in v.get("title_patterns", [])),
            "bottom": sum(1 for v in bottom_data if "compilation" in v.get("title_patterns", [])),
        },
    }

    # ── Key recommendations ──
    analysis["key_recommendations"] = _generate_recommendations(analysis, top_data, bottom_data)

    return analysis


def _generate_recommendations(analysis, top_data, bottom_data):
    recs = []

    # Opening style
    top_openings = analysis["opening_patterns"]["top"]
    if top_openings.get("cold_open", 0) > top_openings.get("host_intro", 0):
        recs.append(
            "Cold opens dominate top videos. Start with the story or a dramatic quote, "
            "not a host introduction."
        )
    elif top_openings.get("host_intro", 0) > top_openings.get("cold_open", 0):
        recs.append(
            "Host intros are common in top videos. A brief, energetic greeting before "
            "diving into content works well."
        )

    # Pacing
    pacing = analysis["pacing_differences"]
    top_plug_n = int(pacing["channel_plug_in_first_60s"]["top"].split("/")[0])
    bot_plug_n = int(pacing["channel_plug_in_first_60s"]["bottom"].split("/")[0])
    top_total = int(pacing["channel_plug_in_first_60s"]["top"].split("/")[1])
    bot_total = int(pacing["channel_plug_in_first_60s"]["bottom"].split("/")[1])
    if top_total > 0 and bot_total > 0:
        top_rate = top_plug_n / top_total
        bot_rate = bot_plug_n / bot_total
        if top_rate < bot_rate:
            recs.append(
                "Top videos are LESS likely to include channel plugs in the first 60 seconds. "
                "Save promotions for later in the video."
            )
        elif top_rate > bot_rate:
            recs.append(
                "Top videos tend to include a brief channel mention early. "
                "A quick plug in the first 60s may help without hurting engagement."
            )

    # Topics
    topic_corr = analysis["topic_correlation"]
    top_favored = list(topic_corr["topics_favoring_top"].keys())[:3]
    if top_favored:
        recs.append(
            f"Topics that strongly correlate with high views: {', '.join(top_favored)}. "
            "Consider prioritizing these themes."
        )

    # Title patterns
    tp = analysis["title_patterns"]
    top_tp = tp["top"]
    bot_tp = tp["bottom"]
    if top_tp.get("ellipsis", 0) / max(len(top_data), 1) > bot_tp.get("ellipsis", 0) / max(len(bottom_data), 1):
        recs.append("Ellipsis in titles (...) correlates with higher views. Use cliffhanger titles.")
    if top_tp.get("pipe_separator", 0) / max(len(top_data), 1) > bot_tp.get("pipe_separator", 0) / max(len(bottom_data), 1):
        recs.append("Using '|' separator with 'Reddit Stories' tag in titles correlates with top performance.")

    # Duration
    cs = analysis["content_structure"]
    if cs["avg_duration_min"]["top"] > cs["avg_duration_min"]["bottom"] * 1.3:
        recs.append(
            f"Top videos average {cs['avg_duration_min']['top']:.0f} min vs "
            f"{cs['avg_duration_min']['bottom']:.0f} min for bottom. Longer content performs better."
        )
    elif cs["avg_duration_min"]["bottom"] > cs["avg_duration_min"]["top"] * 1.3:
        recs.append(
            f"Bottom videos are longer ({cs['avg_duration_min']['bottom']:.0f} min) vs "
            f"top ({cs['avg_duration_min']['top']:.0f} min). Tighter editing may help."
        )

    if not recs:
        recs.append("Analysis complete. See detailed breakdowns in other sections.")

    return recs


def process_video(video, proxy_index=0, max_retries=3):
    """Process a single video: fetch transcript and extract features."""
    vid = video["video_id"]
    title = video["title"]

    for attempt in range(max_retries):
        proxy = PROXIES[proxy_index % len(PROXIES)]
        segments, err = fetch_transcript_yt_dlp(vid, proxy=proxy)

        if segments:
            break
        elif err and "429" in str(err):
            proxy_index = (proxy_index + 1) % len(PROXIES)
            time.sleep(2)
        elif err and "timeout" in str(err).lower():
            proxy_index = (proxy_index + 1) % len(PROXIES)
            time.sleep(1)
        else:
            break  # non-retryable error

    if not segments:
        return {
            "video_id": vid,
            "title": title,
            "views": video["view_count"],
            "duration_min": video["duration_minutes"],
            "publish_date": video["publish_date"],
            "first_30s": "",
            "first_60s": "",
            "opening_style": "unknown",
            "detected_host": "Unknown",
            "topics": detect_topics(title, ""),
            "has_channel_plug_in_first_60s": False,
            "title_patterns": classify_title_pattern(title),
            "transcript_error": err or "unknown error",
        }, proxy_index

    text_30 = extract_intro_text(segments, 30)
    text_60 = extract_intro_text(segments, 60)

    result = {
        "video_id": vid,
        "title": title,
        "views": video["view_count"],
        "duration_min": video["duration_minutes"],
        "publish_date": video["publish_date"],
        "first_30s": text_30,
        "first_60s": text_60,
        "opening_style": classify_opening(text_30),
        "detected_host": detect_host(text_60),
        "topics": detect_topics(title, text_60),
        "has_channel_plug_in_first_60s": has_channel_plug(text_60),
        "title_patterns": classify_title_pattern(title),
    }

    return result, proxy_index


def refresh_proxies():
    """Try to get fresh working proxies."""
    import requests
    try:
        resp = requests.get(
            "https://api.proxyscrape.com/v3/free-proxy-list/get"
            "?request=displayproxies&protocol=http&timeout=5000",
            timeout=10,
        )
        candidates = [p.strip() for p in resp.text.strip().split("\n") if p.strip()]
        working = []
        for proxy_str in candidates[:30]:
            try:
                p = {"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}
                r = requests.get(
                    "https://www.youtube.com/",
                    proxies=p, timeout=5,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if r.status_code == 200:
                    working.append(proxy_str)
                    if len(working) >= 3:
                        break
            except Exception:
                pass
        if working:
            return working
    except Exception:
        pass
    return PROXIES  # fallback


def main():
    print("=" * 70)
    print("  OKStorytime Deep Transcript Analysis")
    print("=" * 70)

    # Load videos
    print("\n[1/5] Loading video data...")
    videos = load_videos(CSV_PATH)
    print(f"  Loaded {len(videos)} videos total.")

    # Select top and bottom
    print("\n[2/5] Selecting top 50 and bottom 50...")
    top_50, bottom_50 = select_top_bottom(videos)
    print(f"  Top 50 view range: {top_50[-1]['view_count']:,} - {top_50[0]['view_count']:,}")
    print(f"  Bottom 50 view range: {bottom_50[0]['view_count']:,} - {bottom_50[-1]['view_count']:,}")

    # Refresh proxies
    print("\n[3/5] Testing proxies...")
    global PROXIES
    fresh = refresh_proxies()
    PROXIES = fresh
    print(f"  Using proxies: {PROXIES}")

    # Process videos
    print("\n[4/5] Fetching transcripts (this will take a while)...")
    proxy_idx = 0
    top_results = []
    bot_results = []
    failed_top = 0
    failed_bot = 0

    print(f"\n  --- TOP 50 VIDEOS ---")
    for i, v in enumerate(top_50):
        result, proxy_idx = process_video(v, proxy_idx)
        top_results.append(result)
        status = "OK" if "transcript_error" not in result else f"FAIL: {result.get('transcript_error', '')[:50]}"
        if "transcript_error" in result:
            failed_top += 1
        print(f"  [{i+1:2d}/50] {status:55s} | {v['view_count']:>10,} views | {v['title'][:50]}")
        time.sleep(1)  # be polite

    print(f"\n  --- BOTTOM 50 VIDEOS (2024+) ---")
    for i, v in enumerate(bottom_50):
        result, proxy_idx = process_video(v, proxy_idx)
        bot_results.append(result)
        status = "OK" if "transcript_error" not in result else f"FAIL: {result.get('transcript_error', '')[:50]}"
        if "transcript_error" in result:
            failed_bot += 1
        print(f"  [{i+1:2d}/50] {status:55s} | {v['view_count']:>10,} views | {v['title'][:50]}")
        time.sleep(1)

    print(f"\n  Transcript fetch summary:")
    print(f"    Top 50: {50 - failed_top} succeeded, {failed_top} failed")
    print(f"    Bottom 50: {50 - failed_bot} succeeded, {failed_bot} failed")

    # Analyze
    print("\n[5/5] Analyzing patterns...")
    # Filter to only successfully fetched for analysis
    top_ok = [r for r in top_results if "transcript_error" not in r]
    bot_ok = [r for r in bot_results if "transcript_error" not in r]
    analysis = analyze_results(top_ok, bot_ok)

    # Save
    output = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "top_50_fetched": len(top_ok),
            "top_50_failed": failed_top,
            "bottom_50_fetched": len(bot_ok),
            "bottom_50_failed": failed_bot,
        },
        "top_50": top_results,
        "bottom_50": bot_results,
        "analysis": analysis,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n  Results saved to: {OUTPUT_PATH}")

    # Print summary
    print("\n" + "=" * 70)
    print("  ANALYSIS SUMMARY")
    print("=" * 70)

    print(f"\n  Opening Patterns:")
    print(f"    Top:    {analysis['opening_patterns']['top']}")
    print(f"    Bottom: {analysis['opening_patterns']['bottom']}")

    print(f"\n  Topic Correlation (top favoring):")
    for topic, vals in list(analysis["topic_correlation"]["topics_favoring_top"].items())[:5]:
        print(f"    {topic}: top={vals['top_pct']}% vs bottom={vals['bottom_pct']}%")

    print(f"\n  Pacing:")
    print(f"    {analysis['pacing_differences']['insight']}")

    print(f"\n  Title Patterns:")
    print(f"    Top:    {analysis['title_patterns']['top']}")
    print(f"    Bottom: {analysis['title_patterns']['bottom']}")

    print(f"\n  Key Recommendations:")
    for i, rec in enumerate(analysis["key_recommendations"], 1):
        print(f"    {i}. {rec}")

    print("\n" + "=" * 70)
    print("  Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()
