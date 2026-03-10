"""
OKStorytime Analysis — reads from saved CSV, prints results cleanly
"""

import csv
import re
import sys

# Fix emoji/unicode in terminal output
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def load_csv(filename="okstorytime_videos.csv"):
    videos = []
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["view_count"] = int(row["view_count"] or 0)
            row["like_count"] = int(row["like_count"] or 0)
            row["comment_count"] = int(row["comment_count"] or 0)
            row["duration_minutes"] = float(row["duration_minutes"] or 0)
            row["duration_seconds"] = int(row["duration_seconds"] or 0)
            row["publish_year"] = int(row["publish_year"] or 0)
            row["publish_month"] = int(row["publish_month"] or 0)
            row["publish_hour"] = int(row["publish_hour"] or 0)
            videos.append(row)
    return videos


def clean(text):
    """Remove non-printable characters for safe terminal output."""
    return text.encode('ascii', errors='replace').decode('ascii')


def analyze(videos):
    by_views = sorted(videos, key=lambda x: x["view_count"], reverse=True)

    print("\n" + "="*65)
    print("OKSTORYTIME CHANNEL ANALYSIS")
    print("="*65)
    print(f"Total videos: {len(videos):,}")
    print(f"Total views:  {sum(v['view_count'] for v in videos):,}")
    avg = sum(v['view_count'] for v in videos) / len(videos)
    print(f"Avg views:    {avg:,.0f}")

    # ── Top 10 ──────────────────────────────────────────────────
    print("\n── TOP 10 VIDEOS BY VIEWS ─────────────────────────────────")
    for i, v in enumerate(by_views[:10], 1):
        print(f"{i:>2}. {v['view_count']:>9,} | {v['duration_minutes']:>5.1f}min | {v['publish_date']} {v['publish_day_of_week'][:3]} | {clean(v['title'])[:65]}")

    # ── Lowest recent ───────────────────────────────────────────
    recent = [v for v in videos if v["publish_year"] >= 2024]
    recent_sorted = sorted(recent, key=lambda x: x["view_count"])
    print(f"\n── LOWEST 10 RECENT VIDEOS (2024+) — {len(recent)} total ──────────")
    for i, v in enumerate(recent_sorted[:10], 1):
        print(f"{i:>2}. {v['view_count']:>9,} | {v['duration_minutes']:>5.1f}min | {v['publish_date']} {v['publish_day_of_week'][:3]} | {clean(v['title'])[:65]}")

    # ── By day of week ──────────────────────────────────────────
    print("\n── AVG VIEWS BY DAY OF WEEK ───────────────────────────────")
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    day_avgs = {}
    for day in days:
        dvids = [v for v in videos if v["publish_day_of_week"] == day]
        if dvids:
            avg_d = sum(v["view_count"] for v in dvids) / len(dvids)
            day_avgs[day] = avg_d
            bar = "█" * int(avg_d / 1000)
            print(f"  {day:<10} | {avg_d:>8,.0f} avg | {len(dvids):>4} videos | {bar}")
    best_day = max(day_avgs, key=day_avgs.get)
    worst_day = min(day_avgs, key=day_avgs.get)
    print(f"  → BEST: {best_day} ({day_avgs[best_day]:,.0f})  |  WORST: {worst_day} ({day_avgs[worst_day]:,.0f})")
    print(f"  → Difference: {day_avgs[best_day]/day_avgs[worst_day]:.1f}x")

    # ── By video length ─────────────────────────────────────────
    print("\n── AVG VIEWS BY VIDEO LENGTH ──────────────────────────────")
    buckets = [
        ("Shorts (<2 min)",  0,   2),
        ("2-10 min",         2,  10),
        ("10-20 min",       10,  20),
        ("20-40 min",       20,  40),
        ("40-60 min",       40,  60),
        ("60-90 min",       60,  90),
        ("90-120 min",      90, 120),
        ("120+ min",       120, 9999),
    ]
    length_avgs = {}
    for label, lo, hi in buckets:
        bvids = [v for v in videos if lo <= v["duration_minutes"] < hi]
        if bvids:
            avg_b = sum(v["view_count"] for v in bvids) / len(bvids)
            length_avgs[label] = avg_b
            print(f"  {label:<18} | {avg_b:>8,.0f} avg | {len(bvids):>4} videos")

    # ── By year (trend) ─────────────────────────────────────────
    print("\n── AVG VIEWS BY YEAR (viewer loss trend) ──────────────────")
    for year in sorted(set(v["publish_year"] for v in videos if v["publish_year"])):
        yvids = [v for v in videos if v["publish_year"] == year]
        avg_y = sum(v["view_count"] for v in yvids) / len(yvids)
        bar = "█" * int(avg_y / 1000)
        print(f"  {year} | {avg_y:>8,.0f} avg | {len(yvids):>4} videos | {bar}")

    # ── Monthly trend (2023+) ────────────────────────────────────
    print("\n── MONTHLY VIEW TREND (2023 onwards) ──────────────────────")
    monthly = {}
    for v in videos:
        if v["publish_year"] >= 2023:
            key = f"{v['publish_year']}-{v['publish_month']:02d}"
            monthly.setdefault(key, []).append(v["view_count"])
    for month in sorted(monthly.keys()):
        vids = monthly[month]
        avg_m = sum(vids) / len(vids)
        bar = "█" * int(avg_m / 500)
        print(f"  {month} | {avg_m:>7,.0f} avg | {len(vids):>3} vids | {bar}")

    # ── Title keyword analysis ───────────────────────────────────
    print("\n── TITLE KEYWORDS: TOP 25% vs BOTTOM 25% ──────────────────")
    top_q = by_views[:len(videos)//4]
    bot_q  = by_views[-(len(videos)//4):]

    def word_freq(vlist):
        freq = {}
        for v in vlist:
            for w in re.findall(r"[a-zA-Z]{4,}", v["title"].lower()):
                freq[w] = freq.get(w, 0) + 1
        return freq

    top_words = word_freq(top_q)
    bot_words  = word_freq(bot_q)

    print("\n  Words 3x+ more common in TOP videos:")
    scored = {w: c / max(bot_words.get(w, 0.5), 0.5)
              for w, c in top_words.items() if c >= 5}
    for w, s in sorted(scored.items(), key=lambda x: -x[1])[:15]:
        print(f"    +{s:>4.1f}x  '{w}'  (top:{top_words[w]}  bot:{bot_words.get(w,0)})")

    print("\n  Words 3x+ more common in BOTTOM videos:")
    scored_b = {w: c / max(top_words.get(w, 0.5), 0.5)
                for w, c in bot_words.items() if c >= 5}
    for w, s in sorted(scored_b.items(), key=lambda x: -x[1])[:15]:
        print(f"    -{s:>4.1f}x  '{w}'  (bot:{bot_words[w]}  top:{top_words.get(w,0)})")

    # ── Shorts vs Long-form breakdown ────────────────────────────
    shorts = [v for v in videos if v["duration_minutes"] < 2]
    longform = [v for v in videos if v["duration_minutes"] >= 2]
    print("\n── SHORTS vs LONG-FORM BREAKDOWN ───────────────────────────")
    if shorts:
        s_avg = sum(v["view_count"] for v in shorts) / len(shorts)
        print(f"  Shorts    (<2 min): {len(shorts):>4} videos | {s_avg:>8,.0f} avg views")
    if longform:
        l_avg = sum(v["view_count"] for v in longform) / len(longform)
        print(f"  Long-form (2+ min): {len(longform):>4} videos | {l_avg:>8,.0f} avg views")

    # ── Shorts trend ─────────────────────────────────────────────
    print("\n── SHORTS PERFORMANCE BY YEAR ──────────────────────────────")
    for year in sorted(set(v["publish_year"] for v in shorts if v["publish_year"])):
        yvids = [v for v in shorts if v["publish_year"] == year]
        avg_y = sum(v["view_count"] for v in yvids) / len(yvids)
        print(f"  {year} | {avg_y:>8,.0f} avg | {len(yvids):>4} Shorts")

    print("\n── LONG-FORM PERFORMANCE BY YEAR ───────────────────────────")
    for year in sorted(set(v["publish_year"] for v in longform if v["publish_year"])):
        yvids = [v for v in longform if v["publish_year"] == year]
        avg_y = sum(v["view_count"] for v in yvids) / len(yvids)
        print(f"  {year} | {avg_y:>8,.0f} avg | {len(yvids):>4} long-form")

    print("\n" + "="*65)


if __name__ == "__main__":
    print("Loading data from okstorytime_videos.csv...")
    videos = load_csv()
    print(f"Loaded {len(videos)} videos.")
    analyze(videos)
