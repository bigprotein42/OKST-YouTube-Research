"""
Generates a full interactive HTML report for OKStorytime
Includes: analytics with Chart.js, thumbnail analysis, action plan, competitor context
"""

import csv, re, os, base64, json, time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import requests as _req

# ── Competitor channels (Reddit story niche) ──────────────────────
COMPETITORS = [
    # ── Core competitors ──
    {"name": "Two Hot Takes",     "id": "UCvUW0xT38Ho7qyUmBgBZXQA"},
    {"name": "rSlash",            "id": "UC0-swBG9Ne0Vh4OuoJ2bjbA"},
    {"name": "MrBallen",          "id": "UCtPrkXdtCM5DACLufB9jbsA"},
    {"name": "Comfort Level",     "id": "UCJ8l9Mu5FOSQ1WFFhS4mlDA"},
    {"name": "Charlotte Dobre",   "id": "UCwc_RHwAPPaEh-jtwClpVrg"},
    {"name": "Am I the Jerk",     "id": "UCZKLuU6t7CaB_RD-bD4qdWw"},
    {"name": "Mark Narrations",   "id": "UCcmyNcmduQbuDrHxpL_3ojw"},
    # ── Scouted competitors ──
    {"name": "Reddit On Wiki",    "id": "UCOzxm6gtNWnoazoSScCp3Yg"},
    {"name": "Mr. Redder",        "id": "UCnDN_qGjDX_EfZZklyg7fqQ"},
    {"name": "Updoot Studios",    "id": "UCEqKKebvZbAQoD3NRIn4jaQ"},
    {"name": "Karma Stories",     "id": "UCqH-qoS5rU2pEKPtWnfCzHw"},
    {"name": "rSpace",            "id": "UCj0VjNM-ULRLWmbgAWlV27g"},
    {"name": "Ripe Stories",      "id": "UCR3z2WipuYNVRjnSWCBdG-A"},
    {"name": "Reddit Tales",      "id": "UCIGfmvaA5a0D65jrEln14qA"},
    {"name": "Lost Genre",        "id": "UC9wROd77URIUgUOtsN37pKQ"},
]

def _jpeg_is_portrait(path):
    """Return True if JPEG image is taller than wide (= a Short). No PIL needed."""
    try:
        with open(path, "rb") as f:
            data = f.read(65536)  # read first 64KB — enough for headers
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                break
            marker = data[i+1]
            if marker in (0xC0, 0xC1, 0xC2):  # SOF markers contain dimensions
                h = (data[i+5] << 8) | data[i+6]
                w = (data[i+7] << 8) | data[i+8]
                return h > w
            if marker in (0xD8, 0xD9, 0xDA):
                break
            length = (data[i+2] << 8) | data[i+3]
            i += 2 + length
    except Exception:
        pass
    return False


def _looks_like_short(title):
    """Heuristic: detect Shorts by title patterns."""
    t = title.lower().strip()
    # Explicit #shorts tag
    if "#shorts" in t or "#short" in t:
        return True
    # Very short titles (under 30 chars) with no subreddit tag are often Shorts
    # But don't filter titles with r/ (those are rSlash-style long-form)
    return False


def _jpeg_dimensions(path):
    """Return (width, height) of a JPEG without PIL."""
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                break
            marker = data[i+1]
            if marker in (0xC0, 0xC1, 0xC2):
                h = (data[i+5] << 8) | data[i+6]
                w = (data[i+7] << 8) | data[i+8]
                return w, h
            if marker in (0xD8, 0xD9, 0xDA):
                break
            length = (data[i+2] << 8) | data[i+3]
            i += 2 + length
    except Exception:
        pass
    return 0, 0


def _parse_iso_duration(d):
    """Convert ISO 8601 duration to seconds."""
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', d or "")
    if not m:
        return 0
    return int(m.group(1) or 0)*3600 + int(m.group(2) or 0)*60 + int(m.group(3) or 0)


COMP_HISTORY_CSV = "competitor_history.csv"


def _load_comp_history():
    """Load cumulative competitor video history from CSV."""
    history = {}
    if os.path.exists(COMP_HISTORY_CSV):
        with open(COMP_HISTORY_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["view_count"] = int(row.get("view_count", 0))
                row["duration_seconds"] = int(row.get("duration_seconds", 0))
                history[row["video_id"]] = row
    return history


def _save_comp_history(history):
    """Save competitor history to CSV."""
    if not history:
        return
    fields = ["channel", "video_id", "title", "published_date", "published",
              "url", "view_count", "duration_seconds"]
    with open(COMP_HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in sorted(history.values(), key=lambda x: x.get("published_date", ""), reverse=True):
            w.writerow(row)


def fetch_competitor_thumbs():
    """Pull long-form-only thumbnails from competitor RSS feeds, with cumulative history."""
    os.makedirs("thumbnails/competitors", exist_ok=True)

    # Load existing history
    history = _load_comp_history()
    existing_ids = set(history.keys())

    # Fetch ALL videos from RSS (no date cutoff — RSS gives ~15 per channel)
    new_items = []
    for ch in COMPETITORS:
        try:
            rss = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['id']}"
            r = _req.get(rss, timeout=10)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            ns = {"a": "http://www.w3.org/2005/Atom",
                  "yt": "http://www.youtube.com/xml/schemas/2015",
                  "media": "http://search.yahoo.com/mrss/"}
            for entry in root.findall("a:entry", ns):
                vid  = entry.find("yt:videoId", ns).text
                pub  = entry.find("a:published", ns).text
                title= entry.find("a:title", ns).text or ""
                dt   = datetime.fromisoformat(pub)
                vc = 0
                stats = entry.find("media:group/media:community/media:statistics", ns)
                if stats is not None:
                    vc = int(stats.get("views", 0))
                item = {"channel": ch["name"], "video_id": vid,
                        "title": title, "published_date": dt.strftime("%Y-%m-%d"),
                        "published": dt.strftime("%b %d"),
                        "url": f"https://youtube.com/watch?v={vid}",
                        "view_count": vc, "duration_seconds": 0}
                # Update view counts for existing items, add new ones
                if vid in history:
                    history[vid]["view_count"] = vc  # refresh view count
                else:
                    new_items.append(item)
        except Exception as e:
            print(f"  ✗ {ch['name']}: {e}")

    # Filter Shorts from NEW items only (existing ones already filtered)
    if new_items:
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
        if api_key:
            all_ids = [it["video_id"] for it in new_items]
            meta = {}
            for i in range(0, len(all_ids), 50):
                batch = all_ids[i:i+50]
                try:
                    r = _req.get(
                        "https://www.googleapis.com/youtube/v3/videos",
                        params={"part": "contentDetails,statistics", "id": ",".join(batch), "key": api_key},
                        timeout=10)
                    for v in r.json().get("items", []):
                        meta[v["id"]] = {
                            "duration_seconds": _parse_iso_duration(v["contentDetails"].get("duration", "")),
                            "view_count": int(v["statistics"].get("viewCount", 0))}
                except Exception as e:
                    print(f"  ✗ API duration fetch: {e}")
            for it in new_items:
                it.update(meta.get(it["video_id"], {}))
            before = len(new_items)
            new_items = [it for it in new_items if it["duration_seconds"] > 120]
            print(f"  Filtered {before - len(new_items)} Shorts via API; {len(new_items)} new long-form")
        else:
            before = len(new_items)
            long_form = []
            for it in new_items:
                try:
                    r = _req.head(f"https://www.youtube.com/shorts/{it['video_id']}",
                                  allow_redirects=False, timeout=5)
                    if r.status_code == 200:
                        continue
                except:
                    pass
                long_form.append(it)
                time.sleep(0.05)
            new_items = long_form
            print(f"  Filtered {before - len(new_items)} Shorts via URL check; {len(new_items)} new long-form")

    # Merge new items into history
    for it in new_items:
        history[it["video_id"]] = it
    print(f"  {len(new_items)} new videos added to history ({len(history)} total)")

    # Save updated history
    _save_comp_history(history)

    # Download thumbnails for all items in history
    all_items = list(history.values())
    for item in all_items:
        path = f"thumbnails/competitors/{item['video_id']}.jpg"
        if os.path.exists(path):
            continue
        for q in ["maxresdefault", "hqdefault", "mqdefault"]:
            try:
                r = _req.get(f"https://i.ytimg.com/vi/{item['video_id']}/{q}.jpg", timeout=8)
                if r.status_code == 200 and len(r.content) > 5000:
                    with open(path, "wb") as f:
                        f.write(r.content)
                    break
            except:
                pass
        time.sleep(0.05)

    # Filter out portrait thumbnails
    before = len(all_items)
    filtered = []
    for item in all_items:
        path = f"thumbnails/competitors/{item['video_id']}.jpg"
        if os.path.exists(path):
            w, h = _jpeg_dimensions(path)
            if h > w and w > 0:
                os.remove(path)
                del history[item["video_id"]]
                continue
            if w > 0 and h > 0 and w / h < 1.4:
                os.remove(path)
                del history[item["video_id"]]
                continue
        filtered.append(item)
    if before - len(filtered):
        print(f"  Removed {before - len(filtered)} portrait/Shorts thumbnails")
        _save_comp_history(history)

    return filtered


def load_csv(filename="okstorytime_videos.csv"):
    videos = []
    with open(filename, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["view_count"]       = int(row["view_count"] or 0)
            row["like_count"]       = int(row["like_count"] or 0)
            row["comment_count"]    = int(row["comment_count"] or 0)
            row["duration_minutes"] = float(row["duration_minutes"] or 0)
            row["publish_year"]     = int(row["publish_year"] or 0)
            row["publish_month"]    = int(row["publish_month"] or 0)
            # Defaults for studio analytics fields
            row.setdefault("impressions", 0)
            row.setdefault("ctr_pct", 0)
            row.setdefault("watch_time_hours", 0)
            row.setdefault("avg_view_minutes", 0)
            row.setdefault("avg_pct_viewed", 0)
            row.setdefault("estimated_revenue_usd", 0)
            row.setdefault("rpm_usd", 0)
            row.setdefault("cpm_usd", 0)
            row.setdefault("subscribers_gained", 0)
            videos.append(row)

    # Merge first-24h CTR if available (from fetch_analytics.py)
    first24h_file = "okstorytime_first24h_ctr.csv"
    if os.path.exists(first24h_file):
        ctr24_map = {}
        with open(first24h_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ctr24_map[row["video_id"]] = row

        matched_24h = 0
        for v in videos:
            c = ctr24_map.get(v.get("video_id", ""))
            if c:
                v["ctr_24h_pct"]      = round(float(c.get("ctr_24h_pct", 0) or 0), 2)
                v["views_24h"]        = int(float(c.get("views_24h", 0) or 0))
                v["impressions_24h"]  = int(float(c.get("impressions_24h", 0) or 0))
                matched_24h += 1
        print(f"  Merged first-24h CTR for {matched_24h}/{len(videos)} videos")
    else:
        for v in videos:
            v.setdefault("ctr_24h_pct", 0)
            v.setdefault("views_24h", 0)
            v.setdefault("impressions_24h", 0)

    # Merge Studio analytics if available
    studio_file = "okstorytime_studio_analytics.csv"
    if os.path.exists(studio_file):
        studio_map = {}
        with open(studio_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                studio_map[row["video_id"]] = row

        matched = 0
        for v in videos:
            s = studio_map.get(v.get("video_id", ""))
            if s:
                v["impressions"]           = int(float(s.get("impressions", 0) or 0))
                v["ctr_pct"]               = round(float(s.get("ctr_pct", 0) or 0), 2)
                v["watch_time_hours"]      = round(float(s.get("watch_time_hours", 0) or 0), 1)
                v["avg_view_minutes"]      = round(float(s.get("avg_view_minutes", 0) or 0), 1)
                v["avg_pct_viewed"]        = round(float(s.get("avg_pct_viewed", 0) or 0), 1)
                v["estimated_revenue_usd"] = round(float(s.get("estimated_revenue_usd", 0) or 0), 2)
                v["rpm_usd"]               = round(float(s.get("rpm_usd", 0) or 0), 2)
                v["cpm_usd"]               = round(float(s.get("cpm_usd", 0) or 0), 2)
                v["subscribers_gained"]    = int(float(s.get("subscribers_gained", 0) or 0))
                matched += 1
        print(f"  Merged studio analytics for {matched}/{len(videos)} videos")

    return videos


def img_b64(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def word_freq(vlist):
    freq = {}
    for v in vlist:
        for w in re.findall(r"[a-zA-Z]{4,}", v["title"].lower()):
            freq[w] = freq.get(w, 0) + 1
    return freq


def fmt(n): return f"{n:,.0f}"


def build(videos):
    # Fetch competitor thumbnails upfront
    print("Fetching competitor thumbnails from RSS feeds...")
    comp_items = fetch_competitor_thumbs()
    print(f"  Found {len(comp_items)} competitor videos in history")

    by_views    = sorted(videos, key=lambda x: x["view_count"], reverse=True)
    total_views = sum(v["view_count"] for v in videos)
    avg_views   = total_views / len(videos)

    # Studio analytics aggregates (CTR, revenue, impressions)
    has_studio = any(v.get("impressions", 0) > 0 for v in videos)
    lf_vids = [v for v in videos if v["duration_minutes"] >= 5]
    lf_with_ctr = [v for v in lf_vids if v.get("ctr_pct", 0) > 0]
    avg_ctr_lf = round(sum(v["ctr_pct"] for v in lf_with_ctr) / len(lf_with_ctr), 1) if lf_with_ctr else 0
    total_revenue = sum(v.get("estimated_revenue_usd", 0) for v in videos)
    total_impressions = sum(v.get("impressions", 0) for v in videos)
    total_watch_hours = sum(v.get("watch_time_hours", 0) for v in videos)

    # Day of week (all time default)
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    day_data = {}
    for day in days:
        dv = [v for v in videos if v["publish_day_of_week"] == day]
        if dv:
            day_data[day] = {"avg": sum(v["view_count"] for v in dv)/len(dv), "count": len(dv)}
    max_day = max(d["avg"] for d in day_data.values())

    # Length buckets (long-form only, 5+ min)
    buckets = [
        ("5–10 min", 5, 10), ("10–20 min", 10, 20),
        ("20–40 min", 20, 40), ("40–60 min", 40, 60), ("60–90 min", 60, 90),
        ("90–120 min", 90, 120), ("120+ min", 120, 9999),
    ]
    length_data = []
    for label, lo, hi in buckets:
        bv = [v for v in videos if lo <= v["duration_minutes"] < hi]
        if bv:
            length_data.append({"label": label, "avg": sum(v["view_count"] for v in bv)/len(bv), "count": len(bv)})
    max_len = max(d["avg"] for d in length_data)

    # Year data
    years = sorted(set(v["publish_year"] for v in videos if v["publish_year"]))
    year_data, shorts_yr, long_yr = {}, {}, {}
    shorts   = [v for v in videos if v["duration_minutes"] < 2]
    longform = [v for v in videos if v["duration_minutes"] >= 5]
    for yr in years:
        yv = [v for v in videos if v["publish_year"] == yr]
        year_data[yr] = {"avg": sum(v["view_count"] for v in yv)/len(yv), "count": len(yv)}
        sv = [v for v in shorts   if v["publish_year"] == yr]
        lv = [v for v in longform if v["publish_year"] == yr]
        if sv: shorts_yr[yr] = sum(v["view_count"] for v in sv)/len(sv)
        if lv: long_yr[yr]   = sum(v["view_count"] for v in lv)/len(lv)

    # Monthly data — ALL years for chart (long-form 5+ min only), multi-metric
    all_monthly = {}
    for v in videos:
        if v["publish_year"] >= 2022 and v["duration_minutes"] >= 5:
            k = f"{v['publish_year']}-{v['publish_month']:02d}"
            all_monthly.setdefault(k, []).append(v)
    monthly_chart_data = []
    for k, vids_in_month in sorted(all_monthly.items()):
        n = len(vids_in_month)
        total_views = sum(v["view_count"] for v in vids_in_month)
        total_wh = sum(v.get("watch_time_hours", 0) for v in vids_in_month)
        avg_dur = sum(v["duration_minutes"] for v in vids_in_month) / n
        ctrs = [v.get("ctr_pct", 0) for v in vids_in_month if v.get("ctr_pct", 0) > 0]
        avg_ctr = (sum(ctrs) / len(ctrs)) if ctrs else 0
        avg_pcts = [v.get("avg_pct_viewed", 0) for v in vids_in_month if v.get("avg_pct_viewed", 0) > 0]
        avg_pct = (sum(avg_pcts) / len(avg_pcts)) if avg_pcts else 0
        total_rev = sum(v.get("estimated_revenue_usd", 0) for v in vids_in_month)
        monthly_chart_data.append({
            "month": k, "count": n,
            "views": total_views,
            "avg_views": round(total_views / n),
            "watch_hours": round(total_wh, 1),
            "avg_duration": round(avg_dur, 1),
            "avg_pct_viewed": round(avg_pct, 1),
            "ctr": round(avg_ctr, 2),
            "revenue": round(total_rev, 2),
        })
    monthly_json = json.dumps(monthly_chart_data)

    # Monthly for legacy table (2023+, long-form 5+ min only)
    monthly = {}
    for v in videos:
        if v["publish_year"] >= 2023 and v["duration_minutes"] >= 5:
            k = f"{v['publish_year']}-{v['publish_month']:02d}"
            monthly.setdefault(k, []).append(v["view_count"])
    monthly_avgs = {k: sum(v)/len(v) for k, v in monthly.items()}
    max_mo = max(monthly_avgs.values())

    # ── Per-type monthly data (for Decline Timeline tabs) ────────
    def _is_live(v):
        t = v.get("title", "")
        return "🔴" in t or bool(re.search(r'\bstream\b|\blive\b|\bvod\b', t, re.IGNORECASE))

    longform_vids = [v for v in videos if v["duration_minutes"] >= 5 and not _is_live(v)]
    shorts_vids   = [v for v in videos if v["duration_minutes"] < 2]
    live_vids     = [v for v in videos if _is_live(v)]

    def _monthly_avgs_for(vlist):
        mo = {}
        for v in vlist:
            if v["publish_year"] >= 2022:
                k = f"{v['publish_year']}-{v['publish_month']:02d}"
                mo.setdefault(k, []).append(v["view_count"])
        return {k: sum(vals)/len(vals) for k, vals in sorted(mo.items())}

    monthly_longform = _monthly_avgs_for(longform_vids)
    monthly_shorts   = _monthly_avgs_for(shorts_vids)
    monthly_live     = _monthly_avgs_for(live_vids)

    def _quick_stats_for(vlist, label):
        """Compute quick stats for a video subset."""
        if not vlist:
            return []
        total = sum(v["view_count"] for v in vlist)
        avg = total / len(vlist)
        best_vid = max(vlist, key=lambda v: v["view_count"])
        best_views = best_vid["view_count"]
        best_str = f"{best_views/1_000_000:.1f}M" if best_views >= 1_000_000 else f"{best_views:,}"

        # Best/worst posting day
        day_avgs = {}
        for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
            dv = [v for v in vlist if v["publish_day_of_week"] == d]
            if dv:
                day_avgs[d] = sum(v["view_count"] for v in dv)/len(dv)
        best_day = max(day_avgs, key=day_avgs.get) if day_avgs else "N/A"
        worst_day = min(day_avgs, key=day_avgs.get) if day_avgs else "N/A"

        # Year trends
        year_avgs = {}
        for v in vlist:
            year_avgs.setdefault(v["publish_year"], []).append(v["view_count"])
        year_avgs = {yr: sum(vs)/len(vs) for yr, vs in year_avgs.items()}
        peak_year = max(year_avgs, key=year_avgs.get) if year_avgs else "N/A"
        latest_year = max(year_avgs.keys()) if year_avgs else "N/A"

        rows = [
            ("Best single video", f'{best_str} views', ""),
            (f"Total {label} videos", f'{len(vlist):,}', ""),
            ("Overall avg views", f'{avg:,.0f}', ""),
        ]
        if day_avgs:
            rows.append(("Best posting day", f'{best_day} ({day_avgs[best_day]:,.0f} avg)', "green"))
            rows.append(("Worst posting day", f'{worst_day} ({day_avgs[worst_day]:,.0f} avg)', "red"))
        if peak_year != "N/A":
            rows.append((f"Peak year ({peak_year})", f'{year_avgs[peak_year]:,.0f} avg views', "green"))
        if latest_year != "N/A" and latest_year != peak_year:
            rows.append((f"Current ({latest_year})", f'{year_avgs[latest_year]:,.0f} avg views',
                         "red" if year_avgs.get(latest_year, 0) < year_avgs.get(peak_year, 0) else "green"))
        return rows

    stats_longform = _quick_stats_for(longform_vids, "long-form")
    stats_shorts   = _quick_stats_for(shorts_vids, "shorts")
    stats_live     = _quick_stats_for(live_vids, "live")

    def _decline_table_html(mo_avgs):
        """Build decline timeline table rows from monthly averages dict."""
        if not mo_avgs:
            return '<tr><td colspan="3" style="color:var(--text-muted)">No data</td></tr>'
        # Pick key inflection points: peak, first big drop, recovery, current low, latest
        items = sorted(mo_avgs.items())
        if not items:
            return '<tr><td colspan="3" style="color:var(--text-muted)">No data</td></tr>'
        peak_k = max(items, key=lambda x: x[1])
        trough_k = min(items, key=lambda x: x[1])
        latest_k = items[-1]
        # Show up to 8 key months: peak, trough, latest, plus a few others
        key_months = set()
        key_months.add(items[0][0])   # first month
        key_months.add(peak_k[0])     # peak
        key_months.add(trough_k[0])   # trough
        key_months.add(latest_k[0])   # latest
        # Add a few evenly spaced ones
        step = max(1, len(items) // 5)
        for i in range(0, len(items), step):
            key_months.add(items[i][0])
        # Build rows sorted chronologically
        rows = ""
        for k, avg in items:
            if k not in key_months:
                continue
            # Color coding
            if avg == peak_k[1]:
                cls = "green"
                note = "Peak"
            elif avg == trough_k[1]:
                cls = "red"
                note = "Lowest"
            elif avg >= peak_k[1] * 0.7:
                cls = "green"
                note = ""
            elif avg <= peak_k[1] * 0.3:
                cls = "red"
                note = ""
            else:
                cls = ""
                note = ""
            # Format month label
            try:
                dt = datetime.strptime(k, "%Y-%m")
                label = dt.strftime("%b %Y")
            except Exception:
                label = k
            count_for_month = len([v for v in videos if f"{v['publish_year']}-{v['publish_month']:02d}" == k])
            note_str = f" — {note}" if note else ""
            rows += f'<tr><td>{label}</td><td class="num {cls}">{avg:,.0f}</td><td class="muted">{count_for_month} videos{note_str}</td></tr>'
        return rows

    def _stats_table_html(stats_rows):
        """Build quick stats table rows."""
        if not stats_rows:
            return '<tr><td colspan="2" style="color:var(--text-muted)">No data</td></tr>'
        rows = ""
        for metric, value, color in stats_rows:
            cls = f' class="num {color}"' if color else ' class="num"'
            rows += f'<tr><td>{metric}</td><td{cls}>{value}</td></tr>'
        return rows

    decline_html_longform = _decline_table_html(monthly_longform)
    decline_html_shorts   = _decline_table_html(monthly_shorts)
    decline_html_live     = _decline_table_html(monthly_live)

    stats_html_longform = _stats_table_html(stats_longform)
    stats_html_shorts   = _stats_table_html(stats_shorts)
    stats_html_live     = _stats_table_html(stats_live)

    # Keywords — long-form only (5+ min, no livestreams)
    lf_by_views = [v for v in by_views if v["duration_minutes"] >= 5 and not _is_live(v)]
    top_q  = lf_by_views[:len(lf_by_views)//4]
    bot_q  = lf_by_views[-(len(lf_by_views)//4):]
    tw, bw = word_freq(top_q), word_freq(bot_q)
    top_kw = sorted([(w, c/max(bw.get(w,.5),.5)) for w,c in tw.items() if c>=5], key=lambda x:-x[1])[:10]
    bot_kw = sorted([(w, c/max(tw.get(w,.5),.5)) for w,c in bw.items() if c>=5], key=lambda x:-x[1])[:10]

    # Recent low performers (long-form 5+ min only)
    recent = sorted([v for v in videos if v["publish_year"] >= 2024 and v["duration_minutes"] >= 5], key=lambda x: x["view_count"])

    # ── Studio Analytics table (CTR, Revenue, Impressions) ──────
    studio_table_rows = ""
    if has_studio:
        lf_by_rev = sorted(lf_vids, key=lambda x: x.get("estimated_revenue_usd", 0), reverse=True)
        for v in lf_by_rev[:25]:
            title_short = v["title"][:55] + ("..." if len(v["title"]) > 55 else "")
            views = v["view_count"]
            ctr = v.get("ctr_pct", 0)
            impr = v.get("impressions", 0)
            rev = v.get("estimated_revenue_usd", 0)
            rpm = v.get("rpm_usd", 0)
            avd = v.get("avg_view_minutes", 0)
            apv = v.get("avg_pct_viewed", 0)
            ctr_cls = "green" if ctr >= 8 else ("red" if ctr < 4 else "")
            rev_cls = "green" if rev >= 2000 else ""
            studio_table_rows += f'''<tr>
              <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{v["title"]}">{title_short}</td>
              <td class="num">{views:,}</td>
              <td class="num">{impr:,}</td>
              <td class="num {ctr_cls}">{ctr:.1f}%</td>
              <td class="num">{avd:.0f}m</td>
              <td class="num">{apv:.0f}%</td>
              <td class="num {rev_cls}">${rev:,.0f}</td>
              <td class="num">${rpm:.2f}</td>
            </tr>'''

        # CTR buckets for long-form
        ctr_buckets = [
            ("< 3%", 0, 3), ("3-5%", 3, 5), ("5-8%", 5, 8),
            ("8-12%", 8, 12), ("12%+", 12, 100),
        ]
        ctr_bucket_rows = ""
        for label, lo, hi in ctr_buckets:
            bv = [v for v in lf_with_ctr if lo <= v["ctr_pct"] < hi]
            if bv:
                avg_v = sum(v["view_count"] for v in bv) / len(bv)
                avg_rev = sum(v.get("estimated_revenue_usd", 0) for v in bv) / len(bv)
                ctr_bucket_rows += f'<tr><td>{label}</td><td class="num">{len(bv)}</td><td class="num">{avg_v:,.0f}</td><td class="num">${avg_rev:,.0f}</td></tr>'

    # ── Shorts data ──────────────────────────────────────────────
    shorts_all       = [v for v in videos if v["duration_minutes"] < 2]
    shorts_by_views  = sorted(shorts_all, key=lambda x: x["view_count"], reverse=True)
    top_shorts_ids   = [v["video_id"] for v in shorts_by_views[:18]]
    recent_shorts    = sorted([v for v in shorts_all if v["publish_year"] >= 2024], key=lambda x: x["view_count"])
    worst_shorts_ids = [v["video_id"] for v in recent_shorts[:18]]
    # Shorts yearly decline table
    shorts_yearly = []
    for yr in years:
        sv = [v for v in shorts_all if v["publish_year"] == yr]
        if sv:
            shorts_yearly.append({"year": yr, "avg": sum(v["view_count"] for v in sv)/len(sv), "count": len(sv)})

    # ── Thumbnails: TRUE LONG-FORM (5+ min horizontal) ───────────
    LONGFORM_MIN = 5  # minutes — excludes Shorts and near-Shorts uploaded as long-form
    longform_by_views = [v for v in by_views if v["duration_minutes"] >= LONGFORM_MIN]
    top_thumb_ids = [v["video_id"] for v in longform_by_views[:18]]
    recent_longform = [v for v in recent if v["duration_minutes"] >= LONGFORM_MIN]
    bot_thumb_ids = [v["video_id"] for v in recent_longform[:18]]

    # Build per-year top pools for filterable grid (top 10 per year max)
    thumb_pool_ids = set(top_thumb_ids)
    for yr in sorted(years, reverse=True)[-5:]:   # last 5 years only
        yr_top = [v for v in longform_by_views if v["publish_year"] == yr][:10]
        thumb_pool_ids |= {v["video_id"] for v in yr_top}
    # Last 30 days
    cutoff_30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    recent_30 = [v for v in longform_by_views if v.get("publish_date","") >= cutoff_30][:10]
    thumb_pool_ids |= {v["video_id"] for v in recent_30}

    # Download missing thumbnails (hqdefault = smaller ~15KB vs maxres ~60KB)
    os.makedirs("thumbnails/top", exist_ok=True)
    os.makedirs("thumbnails/bottom", exist_ok=True)
    shorts_thumb_ids = set(top_shorts_ids) | set(worst_shorts_ids)
    all_thumb_ids = thumb_pool_ids | set(bot_thumb_ids) | shorts_thumb_ids
    for vid_id in all_thumb_ids:
        # Try top folder first, then bottom
        path_top = f"thumbnails/top/{vid_id}.jpg"
        path_bot = f"thumbnails/bottom/{vid_id}.jpg"
        if os.path.exists(path_top) or os.path.exists(path_bot):
            continue
        dest = path_top if vid_id in thumb_pool_ids else path_bot
        for q in ["hqdefault", "mqdefault"]:
            try:
                r = _req.get(f"https://i.ytimg.com/vi/{vid_id}/{q}.jpg", timeout=8)
                if r.status_code == 200 and len(r.content) > 3000:
                    with open(dest, "wb") as f:
                        f.write(r.content)
                    break
            except:
                pass
        time.sleep(0.03)

    # Build thumb dict for JS — only IDs that have a file
    thumb_dict = {}
    for vid_id in thumb_pool_ids:
        b64 = img_b64(f"thumbnails/top/{vid_id}.jpg") or img_b64(f"thumbnails/recent/{vid_id}.jpg")
        if b64:
            thumb_dict[vid_id] = b64
    thumb_dict_json = json.dumps(thumb_dict)
    print(f"  Thumb pool: {len(thumb_dict)} thumbnails embedded for filter grid")

    def thumb_grid(ids, folder):
        html = '<div class="thumb-grid">'
        for vid_id in ids:
            b64 = img_b64(f"thumbnails/{folder}/{vid_id}.jpg") or img_b64(f"thumbnails/top/{vid_id}.jpg") or img_b64(f"thumbnails/bottom/{vid_id}.jpg")
            if b64:
                v     = next((x for x in videos if x["video_id"] == vid_id), {})
                views = fmt(v.get("view_count", 0))
                mins  = v.get("duration_minutes", 0)
                date  = v.get("publish_date", "")
                title = (v.get("title","")[:45] + "…") if len(v.get("title","")) > 45 else v.get("title","")
                html += f'''<div class="thumb-item">
                    <img src="data:image/jpeg;base64,{b64}" alt="{title}">
                    <div class="thumb-label">
                        <strong>{views} views</strong>
                        <div style="display:flex;gap:6px;margin:3px 0 4px;align-items:center">
                            <span class="thumb-dur">{mins:.0f} min</span>
                            <span style="font-size:.68rem;color:var(--text-muted)">{date}</span>
                        </div>
                        <span>{title}</span>
                    </div>
                </div>'''
        html += "</div>"
        return html

    # Build competitor thumb dict and JSON for JS filtering
    comp_thumb_dict = {}
    comp_json_items = []
    for item in sorted(comp_items, key=lambda x: x.get("view_count", 0), reverse=True):
        path = f"thumbnails/competitors/{item['video_id']}.jpg"
        b64 = img_b64(path)
        if not b64:
            continue
        comp_thumb_dict[item["video_id"]] = b64
        comp_json_items.append({
            "video_id": item["video_id"],
            "channel": item["channel"],
            "title": item.get("title", ""),
            "published_date": item.get("published_date", ""),
            "published": item.get("published", ""),
            "url": item.get("url", ""),
            "view_count": item.get("view_count", 0),
        })
    comp_thumb_dict_json = json.dumps(comp_thumb_dict)
    comp_json = json.dumps(comp_json_items)

    # All competitor items for title list (including ones without thumbnails)
    comp_all_json = json.dumps([{
        "video_id": item["video_id"],
        "channel": item["channel"],
        "title": item.get("title", ""),
        "published_date": item.get("published_date", ""),
        "published": item.get("published", ""),
        "url": item.get("url", ""),
        "view_count": item.get("view_count", 0),
    } for item in sorted(comp_items, key=lambda x: x.get("view_count", 0), reverse=True)])

    # ── Weekly Launch Tracker data ──────────────────────────────
    now_dt = datetime.now(timezone.utc)
    GOAL_VIEWS = 10_000
    GOAL_HOURS = 48

    # Videos from last 7 days, last 14 days, last 30 days
    def _parse_date(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    recent_7d  = []
    recent_14d = []
    recent_30d = []
    for v in videos:
        # Launch tracker: long-form only (5+ min)
        if v["duration_minutes"] < 5:
            continue
        pub = _parse_date(v.get("publish_date", ""))
        if not pub:
            continue
        age = now_dt - pub
        v["_age_hours"] = age.total_seconds() / 3600
        v["_age_days"]  = age.days
        if age.days <= 7:
            recent_7d.append(v)
        if age.days <= 14:
            recent_14d.append(v)
        if age.days <= 30:
            recent_30d.append(v)

    recent_7d.sort(key=lambda x: x.get("publish_date", ""), reverse=True)
    recent_14d.sort(key=lambda x: x.get("publish_date", ""), reverse=True)
    recent_30d.sort(key=lambda x: x.get("publish_date", ""), reverse=True)

    # Goal tracking: how many hit 10K in 48hr window?
    # For videos older than 48hr, we can check if they hit 10K (current views)
    # For newer videos, show progress toward goal
    def _tracker_card(v):
        views = v["view_count"]
        title = v.get("title", "")[:65]
        vid_id = v.get("video_id", "")
        age_h = v.get("_age_hours", 999)
        age_d = v.get("_age_days", 999)
        dur = v.get("duration_minutes", 0)
        is_live = "🔴" in v.get("title", "") or bool(re.search(r'\\bstream\\b|\\blive\\b', v.get("title", ""), re.IGNORECASE))
        is_short = dur < 2

        # Progress toward 10K
        pct = min(100, (views / GOAL_VIEWS) * 100)
        bar_color = "var(--green)" if pct >= 100 else ("var(--yellow)" if pct >= 50 else "var(--red)")

        # Status
        if age_h <= 48:
            hrs_left = max(0, 48 - age_h)
            status = f'<span style="color:var(--yellow);font-weight:700">⏳ {hrs_left:.0f}h left in launch window</span>'
        elif views >= GOAL_VIEWS:
            status = '<span style="color:var(--green);font-weight:700">✅ Hit 10K goal!</span>'
        else:
            status = f'<span style="color:var(--red);font-weight:700">❌ Missed — {views:,} at 48h</span>'

        # Action items based on data
        actions = []
        if age_h <= 6:
            actions = ["Share to community tab + all socials NOW", "Pin a hook question as first comment", "Reply to first 10 comments"]
        elif age_h <= 12:
            if pct < 25:
                actions = ["⚠ SWAP THUMBNAIL immediately", "Rewrite title — lead with drama", "Post TikTok clip driving to video"]
            elif pct < 50:
                actions = ["Consider thumbnail swap", "Push harder on socials", "Post a TikTok clip"]
            else:
                actions = ["Looking good — keep engaging comments", "Post a TikTok clip to boost"]
        elif age_h <= 48:
            if pct < 50:
                actions = ["🚨 SWAP BOTH title AND thumbnail — treat as re-launch", "Post 2nd TikTok clip (different moment)", "Cross-promote in next video intro"]
            elif pct < 100:
                actions = ["Post a follow-up Short with cliffhanger", "Cross-promote in next video"]
            else:
                actions = ["🎉 Riding the wave! Post a follow-up Short", "Cross-promote in next video"]
        else:
            if pct < 50:
                actions = ["Review: was the title drama-first?", "Was there a double-twist cold open?", "Check thumbnail — face fills 80%?"]

        actions_html = "".join(f'<div style="font-size:.78rem;padding:3px 0;color:var(--text-muted)">• {a}</div>' for a in actions)

        # Type badge
        if is_short:
            type_badge = '<span style="background:var(--primary);color:white;padding:2px 8px;border-radius:4px;font-size:.7rem;font-weight:700">SHORT</span>'
        elif is_live:
            type_badge = '<span style="background:var(--red);color:white;padding:2px 8px;border-radius:4px;font-size:.7rem;font-weight:700">LIVE</span>'
        else:
            type_badge = f'<span style="background:var(--green);color:white;padding:2px 8px;border-radius:4px;font-size:.7rem;font-weight:700">{dur:.0f}min</span>'

        # Views velocity
        if age_h > 0:
            v_per_hr = views / age_h
            v_per_day = v_per_hr * 24
            velocity_str = f'{v_per_day:,.0f}/day'
        else:
            velocity_str = "Just uploaded"

        thumb_b64 = img_b64(f"thumbnails/top/{vid_id}.jpg") or img_b64(f"thumbnails/bottom/{vid_id}.jpg")
        thumb_html = f'<img src="data:image/jpeg;base64,{thumb_b64}" style="width:140px;border-radius:8px;flex-shrink:0">' if thumb_b64 else ""

        return f'''<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:10px">
          <div style="display:flex;gap:14px;align-items:flex-start">
            {thumb_html}
            <div style="flex:1;min-width:0">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                {type_badge}
                <a href="https://youtube.com/watch?v={vid_id}" target="_blank" style="font-weight:700;font-size:.88rem;color:var(--text);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{title}</a>
              </div>
              <div style="display:flex;gap:16px;font-size:.82rem;margin-bottom:8px">
                <span><strong>{views:,}</strong> views</span>
                <span style="color:var(--text-muted)">{velocity_str}</span>
                <span style="color:var(--text-muted)">{v.get("publish_date","")}</span>
                <span style="color:var(--text-muted)">{v.get("publish_day_of_week","")}</span>
              </div>
              <div style="background:var(--surface2);border-radius:6px;height:10px;overflow:hidden;margin-bottom:6px">
                <div style="background:{bar_color};height:100%;width:{pct:.0f}%;border-radius:6px;transition:width .3s"></div>
              </div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <span style="font-size:.78rem;font-weight:600">{pct:.0f}% to 10K goal</span>
                {status}
              </div>
              {actions_html}
            </div>
          </div>
        </div>'''

    # Build tracker cards
    tracker_7d_html  = "".join(_tracker_card(v) for v in recent_7d)  if recent_7d  else '<p style="color:var(--text-muted)">No videos in the last 7 days.</p>'
    tracker_14d_html = "".join(_tracker_card(v) for v in recent_14d) if recent_14d else '<p style="color:var(--text-muted)">No videos in the last 14 days.</p>'
    tracker_30d_html = "".join(_tracker_card(v) for v in recent_30d) if recent_30d else '<p style="color:var(--text-muted)">No videos in the last 30 days.</p>'

    # Goal summary stats
    def _goal_summary(vlist):
        if not vlist:
            return {"total": 0, "hit": 0, "missed": 0, "in_window": 0, "avg_views": 0, "best": None}
        hit = len([v for v in vlist if v["view_count"] >= GOAL_VIEWS])
        in_window = len([v for v in vlist if v.get("_age_hours", 999) <= 48])
        missed = len(vlist) - hit - in_window
        avg = sum(v["view_count"] for v in vlist) / len(vlist)
        best = max(vlist, key=lambda v: v["view_count"])
        return {"total": len(vlist), "hit": hit, "missed": missed, "in_window": in_window, "avg_views": avg, "best": best}

    goal_7d  = _goal_summary(recent_7d)
    goal_30d = _goal_summary(recent_30d)

    # ── Load transcript analysis if available ───────────────────
    transcript_deep_html = ""
    ta_path = "transcript_analysis.json"
    if os.path.exists(ta_path):
        try:
            with open(ta_path, "r", encoding="utf-8") as f:
                ta = json.load(f)
            analysis = ta.get("analysis", {})

            # Build comparison tables
            def _ta_video_rows(vlist, limit=20):
                rows = ""
                for v in vlist[:limit]:
                    views = v.get("views", 0)
                    vstr = f"{views/1_000_000:.1f}M" if views >= 1_000_000 else f"{views:,}"
                    style = v.get("opening_style", "unknown")
                    host = v.get("detected_host", "Unknown")
                    topics = ", ".join(v.get("topics", [])[:3])
                    plug = "Yes" if v.get("has_channel_plug_in_first_60s") else "No"
                    first30 = (v.get("first_30s", "") or "")[:120]
                    title = (v.get("title", "") or "")[:60]
                    rows += f'<tr><td style="font-size:.78rem;max-width:250px"><a href="https://youtube.com/watch?v={v.get("video_id","")}" target="_blank">{title}</a></td>'
                    rows += f'<td class="num">{vstr}</td><td>{style}</td><td>{host}</td><td style="font-size:.75rem">{topics}</td><td>{plug}</td></tr>'
                return rows

            top_rows = _ta_video_rows(ta.get("top_50", []))
            bot_rows = _ta_video_rows(ta.get("bottom_50", []))

            # Key findings
            findings_html = ""
            for key, label in [("opening_patterns", "Opening Patterns"), ("host_correlation", "Host Correlation"),
                               ("topic_correlation", "Topic Correlation"), ("pacing_differences", "Pacing"),
                               ("title_patterns", "Title Patterns")]:
                val = analysis.get(key, "")
                if isinstance(val, dict):
                    val = f"<strong>Top:</strong> {val.get('top', 'N/A')}<br><strong>Bottom:</strong> {val.get('bottom', 'N/A')}"
                if val:
                    findings_html += f'<div class="insight green" style="margin-bottom:8px"><strong>{label}:</strong> {val}</div>'

            recs = analysis.get("key_recommendations", [])
            recs_html = ""
            for i, rec in enumerate(recs, 1):
                recs_html += f'<div class="insight yellow" style="margin-bottom:6px"><strong>Recommendation {i}:</strong> {rec}</div>'

            transcript_deep_html = f"""
      <div style="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:12px">
        <button class="ta-tab-btn active" onclick="showTATab('findings',this)" style="padding:7px 16px;border:none;background:none;font-weight:700;font-size:.85rem;cursor:pointer;border-bottom:2px solid var(--primary);color:var(--primary);font-family:inherit;margin-bottom:-2px">Key Findings</button>
        <button class="ta-tab-btn" onclick="showTATab('top',this)" style="padding:7px 16px;border:none;background:none;font-weight:600;font-size:.85rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit;margin-bottom:-2px">Top 50 Videos</button>
        <button class="ta-tab-btn" onclick="showTATab('bottom',this)" style="padding:7px 16px;border:none;background:none;font-weight:600;font-size:.85rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit;margin-bottom:-2px">Bottom 50 Videos</button>
      </div>
      <div id="ta-findings" class="ta-panel">
        {findings_html}
        <div style="margin-top:14px"><div style="font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--primary);margin-bottom:8px">Actionable Recommendations</div>{recs_html}</div>
      </div>
      <div id="ta-top" class="ta-panel" style="display:none">
        <div class="table-wrap"><table style="font-size:.82rem">
          <tr><th>Title</th><th>Views</th><th>Opening</th><th>Host</th><th>Topics</th><th>Plug &lt;60s</th></tr>
          {top_rows}
        </table></div>
      </div>
      <div id="ta-bottom" class="ta-panel" style="display:none">
        <div class="table-wrap"><table style="font-size:.82rem">
          <tr><th>Title</th><th>Views</th><th>Opening</th><th>Host</th><th>Topics</th><th>Plug &lt;60s</th></tr>
          {bot_rows}
        </table></div>
      </div>"""
        except Exception as e:
            print(f"  Warning: Could not load transcript analysis: {e}")

    top_grid = thumb_grid(top_thumb_ids, "top")
    bot_grid = thumb_grid(bot_thumb_ids, "bottom")
    thumb_year_btns = "".join(
        f'<button class="filter-btn" onclick="filterThumbs(\'{yr}\',this)">{yr}</button>'
        for yr in sorted(years, reverse=True)
        if any(v["publish_year"] == yr and v["duration_minutes"] >= 5 for v in videos)
    )

    # JSON data for JS time-period filter (day/length tables + thumbnail filter)
    video_json = json.dumps([{
        "v": v["view_count"],
        "y": v["publish_year"],
        "m": v["publish_month"],
        "d": v["publish_day_of_week"],
        "dur": round(v["duration_minutes"], 1),
        "video_id": v["video_id"],
        "view_count": v["view_count"],
        "duration_minutes": round(v["duration_minutes"], 1),
        "publish_year": v["publish_year"],
        "title": v.get("title", ""),
        "publish_date": v.get("publish_date", ""),
        "ctr": v.get("ctr_pct", 0),
        "impressions": v.get("impressions", 0),
        "revenue": v.get("estimated_revenue_usd", 0),
        "rpm": v.get("rpm_usd", 0),
        "watch_hours": v.get("watch_time_hours", 0),
        "avg_pct_viewed": v.get("avg_pct_viewed", 0),
        "ctr_24h": v.get("ctr_24h_pct", 0),
        "views_24h": v.get("views_24h", 0),
    } for v in videos])

    # Table builders
    def video_rows_html(vlist, color=""):
        r = ""
        for i,v in enumerate(vlist,1):
            mins = v["duration_minutes"]
            dur_badge = '<span class="badge short">Short</span>' if mins < 2 else f'<span class="badge long">{mins:.0f}m</span>'
            num_cls = f"num {color}" if color else "num"
            title = v["title"]
            is_live = '\U0001f534' in title or bool(re.search(r'\bstream\b', title, re.IGNORECASE))
            live_attr = ' data-live="1"' if is_live else ''
            r += f'<tr{live_attr}><td class="rank">{i}</td><td><a href="{v["url"]}" target="_blank">{title[:80]}</a></td><td class="{num_cls}">{fmt(v["view_count"])}</td><td>{dur_badge}</td><td class="muted">{v["publish_date"]}</td><td class="muted">{v["publish_day_of_week"][:3]}</td></tr>'
        return r

    def top10_rows():   return video_rows_html(by_views[:10])
    def recent_low_rows(): return video_rows_html(recent[:10], "red")

    def comp_title_rows():
        if not comp_items:
            return '<p style="color:var(--text-muted)">No competitor data — check back after next refresh.</p>'
        rows = ""
        for item in sorted(comp_items, key=lambda x: x.get("view_count",0), reverse=True):
            vc = item.get("view_count", 0)
            views_str = f"{vc/1_000_000:.1f}M" if vc >= 1_000_000 else (f"{vc//1000}K" if vc >= 1000 else ("" if vc == 0 else str(vc)))
            views_badge = f'<span style="font-size:.72rem;color:var(--text-muted);margin-left:6px">{views_str} views</span>' if views_str else ""
            rows += (f'<div style="display:flex;align-items:baseline;gap:8px;padding:7px 10px;'
                     f'border-radius:8px;background:var(--surface2);border:1px solid var(--border)">'
                     f'<span class="thumb-dur" style="flex-shrink:0">{item["channel"]}</span>'
                     f'<a href="{item["url"]}" target="_blank" style="font-size:.875rem;font-weight:600;color:var(--text);text-decoration:none;flex:1">{item["title"]}</a>'
                     f'{views_badge}'
                     f'<span style="font-size:.72rem;color:var(--text-muted);flex-shrink:0">{item["published"]}</span>'
                     f'</div>')
        return rows

    # Pre-compute top/bottom for each format for JS
    top_longform  = [v for v in by_views  if v["duration_minutes"] >= 5][:20]
    top_shorts_vd = [v for v in by_views  if v["duration_minutes"] <  2][:20]
    bot_longform  = sorted([v for v in videos if v["duration_minutes"] >= 5 and v["publish_year"] >= 2024
                            and "🔴" not in v.get("title","") and "stream" not in v.get("title","").lower()
                            and "live" not in v.get("title","").lower()[:20]],
                           key=lambda x: x["view_count"])[:20]
    bot_shorts_vd = sorted([v for v in videos if v["duration_minutes"] <  2 and v["publish_year"] >= 2024],
                           key=lambda x: x["view_count"])[:20]

    def make_video_table(vlist, color=""):
        hdr = '<table><tr><th>#</th><th>Title</th><th>Views</th><th>Length</th><th>Date</th><th>Day</th></tr>'
        return hdr + video_rows_html(vlist, color) + '</table>'

    def year_rows():
        r = ""
        for yr in years:
            d = year_data[yr]
            s = shorts_yr.get(yr,0)
            l = long_yr.get(yr,0)
            r += f'<tr><td><strong>{yr}</strong></td><td class="num">{fmt(d["avg"])}</td><td class="muted">{d["count"]}</td><td class="red">{fmt(s) if s else "—"}</td><td class="green">{fmt(l) if l else "—"}</td></tr>'
        return r

    def kw_rows(kws, sign, color):
        return "".join(f'<tr><td style="color:{color};font-weight:600">{sign} {w}</td><td class="muted">{s:.1f}×</td></tr>' for w,s in kws)

    def shorts_year_rows():
        r = ""
        peak = max((s["avg"] for s in shorts_yearly), default=1)
        for s in shorts_yearly:
            lf_avg = long_yr.get(s["year"], 0)
            vs = f'{s["avg"]/lf_avg:.1f}× long-form' if lf_avg else "—"
            color = "green" if s["avg"] >= peak * 0.5 else "red"
            r += f'<tr><td><strong>{s["year"]}</strong></td><td class="num {color}">{fmt(s["avg"])}</td><td class="muted">{s["count"]}</td><td class="muted">{vs}</td></tr>'
        return r

    def shorts_thumb_grid(ids):
        html = '<div class="thumb-grid">'
        found = 0
        for vid_id in ids:
            b64 = img_b64(f"thumbnails/top/{vid_id}.jpg") or img_b64(f"thumbnails/bottom/{vid_id}.jpg") or img_b64(f"thumbnails/recent/{vid_id}.jpg")
            if not b64:
                continue
            v     = next((x for x in videos if x["video_id"] == vid_id), {})
            views = fmt(v.get("view_count", 0))
            date  = v.get("publish_date", "")
            title = (v.get("title","")[:45] + "…") if len(v.get("title","")) > 45 else v.get("title","")
            html += f'''<div class="thumb-item">
                <a href="{v.get("url","")}" target="_blank">
                    <img src="data:image/jpeg;base64,{b64}" alt="{title}">
                </a>
                <div class="thumb-label">
                    <strong>{views} views</strong>
                    <span class="badge short" style="margin:3px 0 4px;display:inline-block">Short</span>
                    <span style="font-size:.68rem;color:var(--text-muted);display:block">{date}</span>
                    <span>{title}</span>
                </div>
            </div>'''
            found += 1
        if found == 0:
            html += '<p style="color:var(--text-muted);padding:12px">Thumbnails load after next data refresh.</p>'
        html += "</div>"
        return html

    def shorts_top_grid():
        return shorts_thumb_grid(top_shorts_ids)

    def shorts_bot_grid():
        return shorts_thumb_grid(worst_shorts_ids)

    now = datetime.now().strftime("%B %d, %Y")

    # Note: double-braces {{ }} are escaped braces in f-strings
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OKStorytime — Growth Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {{
  --primary: #7c3aed;
  --primary-light: #a78bfa;
  --primary-bg: #f5f3ff;
  --bg: #f4f6fb;
  --surface: #ffffff;
  --surface2: #fafafa;
  --border: #e5e7eb;
  --border-strong: #d1d5db;
  --text: #111827;
  --text-muted: #6b7280;
  --red: #ef4444;
  --red-bg: #fef2f2;
  --green: #10b981;
  --green-bg: #ecfdf5;
  --yellow: #f59e0b;
  --yellow-bg: #fffbeb;
  --radius: 12px;
  --shadow: 0 1px 3px rgba(0,0,0,.08), 0 4px 16px rgba(0,0,0,.04);
  --shadow-md: 0 4px 6px rgba(0,0,0,.07), 0 10px 30px rgba(0,0,0,.06);
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Inter', -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}}
.header {{
  background: linear-gradient(135deg, #1e1148 0%, #7c3aed 60%, #a78bfa 100%);
  color: white;
  padding: 48px 40px 44px;
}}
.header-top {{ display: flex; align-items: flex-start; gap: 16px; margin-bottom: 28px; }}
.header-icon {{ width: 48px; height: 48px; background: rgba(255,255,255,.15); border-radius: 12px; display: grid; place-items: center; font-size: 1.5rem; flex-shrink: 0; }}
.header h1 {{ font-size: 1.75rem; font-weight: 800; letter-spacing: -.03em; }}
.header-sub {{ opacity: .72; font-size: .875rem; margin-top: 3px; }}
.header-stats {{ display: flex; gap: 16px; flex-wrap: wrap; }}
.hstat {{ background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.18); backdrop-filter: blur(10px); border-radius: 10px; padding: 14px 20px; min-width: 120px; }}
.hstat .val {{ font-size: 1.45rem; font-weight: 800; letter-spacing: -.02em; }}
.hstat .lbl {{ font-size: .7rem; opacity: .72; text-transform: uppercase; letter-spacing: .07em; margin-top: 3px; }}
.nav {{ position: sticky; top: 0; z-index: 100; background: rgba(255,255,255,.92); backdrop-filter: blur(16px); border-bottom: 1px solid var(--border); display: flex; gap: 2px; padding: 8px 20px; overflow-x: auto; }}
.nav-btn {{ background: transparent; color: var(--text-muted); border: none; padding: 7px 14px; border-radius: 8px; cursor: pointer; font-size: .82rem; font-weight: 600; white-space: nowrap; transition: all .15s; font-family: inherit; }}
.nav-btn:hover {{ background: var(--primary-bg); color: var(--primary); }}
.nav-btn.active {{ background: var(--primary); color: white; }}
.tab {{ display: none; padding: 28px 20px 48px; max-width: 1120px; margin: 0 auto; }}
.tab.active {{ display: block; }}
.card {{ background: var(--surface); border-radius: var(--radius); border: 1px solid var(--border); padding: 24px; box-shadow: var(--shadow); margin-bottom: 18px; }}
.card-title {{ font-size: .875rem; font-weight: 700; margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; letter-spacing: -.01em; }}
.insight {{ padding: 13px 16px; border-radius: 8px; margin-bottom: 10px; font-size: .875rem; line-height: 1.6; border-left: 3px solid; }}
.insight.red   {{ background: var(--red-bg);    border-color: var(--red);   }}
.insight.green {{ background: var(--green-bg);  border-color: var(--green); }}
.insight.yellow{{ background: var(--yellow-bg); border-color: var(--yellow);}}
/* Competitor dropdowns */
.comp-dropdown {{ border: 1px solid var(--border); border-radius: 12px; background: var(--surface); overflow: hidden; }}
.comp-dropdown[open] {{ background: var(--bg); }}
.comp-summary {{ padding: 14px 18px; cursor: pointer; display: flex; align-items: center; gap: 6px; list-style: none; user-select: none; }}
.comp-summary::-webkit-details-marker {{ display: none; }}
.comp-summary::before {{ content: '▸'; font-size: 1rem; color: var(--primary); transition: transform .2s; margin-right: 4px; }}
.comp-dropdown[open] .comp-summary::before {{ transform: rotate(90deg); }}
.comp-summary:hover {{ background: var(--primary-bg); }}
.comp-detail {{ padding: 0 18px 18px; }}
.comp-section-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }}
@media (max-width: 900px) {{ .comp-section-grid {{ grid-template-columns: 1fr; }} }}
.comp-section-label {{ font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
th {{ background: var(--surface2); text-align: left; padding: 9px 12px; font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--text-muted); border-bottom: 1px solid var(--border); }}
td {{ padding: 9px 12px; border-bottom: 1px solid var(--border); }}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: #fafbff; }}
.highlight-row td {{ background: var(--yellow-bg) !important; }}
.num {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
.rank {{ color: var(--text-muted); font-weight: 600; width: 32px; }}
.muted {{ color: var(--text-muted); }}
.red {{ color: var(--red); }}
.green {{ color: var(--green); }}
a {{ color: var(--primary); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.bar-cell {{ min-width: 120px; }}
.bar-wrap {{ background: #f0f0f6; border-radius: 4px; overflow: hidden; height: 8px; }}
.bar {{ height: 8px; background: linear-gradient(90deg, var(--primary), var(--primary-light)); border-radius: 4px; transition: width .4s ease; min-width: 3px; }}
.bar.gold {{ background: linear-gradient(90deg, #f59e0b, #fcd34d); }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: .7rem; font-weight: 700; letter-spacing: .02em; }}
.badge.short {{ background: #fee2e2; color: #dc2626; }}
.badge.long  {{ background: var(--primary-bg); color: var(--primary); }}
.tag {{ display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: .72rem; font-weight: 600; margin: 2px; }}
.tag.g {{ background: var(--green-bg); color: #065f46; border: 1px solid #a7f3d0; }}
.tag.r {{ background: var(--red-bg);   color: #991b1b; border: 1px solid #fecaca; }}
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
@media(max-width: 720px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
.formula-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media(max-width: 720px) {{ .formula-grid {{ grid-template-columns: 1fr; }} }}
.formula {{ border-radius: var(--radius); padding: 20px; }}
.formula.do   {{ background: var(--green-bg); border: 1px solid #a7f3d0; }}
.formula.dont {{ background: var(--red-bg);   border: 1px solid #fecaca; }}
.formula-title {{ font-weight: 800; font-size: .9rem; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }}
.formula.do   .formula-title {{ color: #065f46; }}
.formula.dont .formula-title {{ color: #991b1b; }}
.formula ul {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }}
.formula li {{ display: flex; align-items: flex-start; gap: 10px; padding: 8px 12px; border-radius: 8px; font-size: .875rem; line-height: 1.4; }}
.formula.do   li {{ background: rgba(16,185,129,.1); }}
.formula.dont li {{ background: rgba(239,68,68,.07); }}
.formula li .icon {{ flex-shrink: 0; width: 18px; text-align: center; }}
.formula li .lbl {{ font-weight: 700; color: var(--text); margin-right: 4px; }}
.formula li .desc {{ color: var(--text-muted); }}
.thumb-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 14px; margin-top: 14px; }}
.thumb-item {{ border-radius: 10px; overflow: hidden; border: 1px solid var(--border); background: var(--surface); transition: transform .15s, box-shadow .15s; }}
.thumb-item:hover {{ transform: translateY(-2px); box-shadow: var(--shadow-md); }}
.thumb-item img {{ width: 100%; display: block; aspect-ratio: 16/9; object-fit: cover; }}
.thumb-label {{ padding: 8px 10px; }}
.thumb-label strong {{ font-size: .82rem; color: var(--text); display: block; }}
.thumb-dur {{ display: inline-block; background: var(--primary-bg); color: var(--primary); font-size: .68rem; font-weight: 700; padding: 1px 6px; border-radius: 10px; margin: 3px 0 4px; }}
.thumb-label span {{ font-size: .75rem; color: var(--text-muted); display: block; margin-top: 2px; }}
.filter-bar {{ display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; align-items: center; }}
.filter-bar span {{ font-size: .8rem; font-weight: 600; color: var(--text-muted); margin-right: 4px; }}
.filter-btn {{ padding: 6px 14px; border-radius: 20px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--text-muted); font-size: .8rem; font-weight: 600; cursor: pointer; font-family: inherit; transition: all .15s; }}
.filter-btn:hover {{ border-color: var(--primary); color: var(--primary); }}
.filter-btn.active {{ background: var(--primary); color: white; border-color: var(--primary); }}
/* YouTube Studio metric tabs */
.metric-tab {{ padding: 14px 22px; border: none; background: none; cursor: pointer; font-family: inherit; text-align: left; position: relative; min-width: 120px; border-bottom: 3px solid transparent; margin-bottom: -2px; transition: all .15s; }}
.metric-tab:hover {{ background: var(--surface2); }}
.metric-tab.active {{ border-bottom-color: var(--primary); }}
.metric-tab-value {{ font-size: 1.3rem; font-weight: 800; color: var(--text-muted); line-height: 1.2; }}
.metric-tab.active .metric-tab-value {{ color: var(--text); }}
.metric-tab-label {{ font-size: .72rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; margin-top: 2px; white-space: nowrap; }}
.chart-container {{ position: relative; height: 300px; margin-bottom: 8px; }}
.chart-meta {{ display: flex; gap: 20px; margin-top: 12px; flex-wrap: wrap; }}
.chart-stat {{ font-size: .82rem; }}
.chart-stat .cs-val {{ font-weight: 700; color: var(--primary); }}
.chart-stat .cs-lbl {{ color: var(--text-muted); }}
.comp-table td:first-child {{ font-weight: 600; }}
footer {{ text-align: center; color: var(--text-muted); font-size: .75rem; padding: 32px; border-top: 1px solid var(--border); }}
/* ── AI CHAT ────────────────────────────────── */
.chat-wrap {{ display: flex; flex-direction: column; gap: 0; }}
.chat-key-bar {{ display: flex; gap: 8px; align-items: center; padding: 12px 16px; background: var(--primary-bg); border-radius: 10px; margin-bottom: 14px; font-size: .82rem; }}
.chat-key-bar input {{ flex: 1; border: 1px solid var(--border-strong); border-radius: 8px; padding: 6px 10px; font-size: .82rem; font-family: inherit; }}
.chat-key-bar button {{ background: var(--primary); color: white; border: none; border-radius: 8px; padding: 6px 14px; cursor: pointer; font-size: .82rem; font-weight: 600; font-family: inherit; }}
.chat-messages {{ min-height: 200px; max-height: 420px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; padding: 4px 0 16px; }}
.chat-msg {{ display: flex; gap: 10px; align-items: flex-start; }}
.chat-msg.user {{ flex-direction: row-reverse; }}
.chat-avatar {{ width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; font-size: .85rem; flex-shrink: 0; }}
.chat-msg.ai .chat-avatar {{ background: var(--primary-bg); }}
.chat-msg.user .chat-avatar {{ background: #dbeafe; }}
.chat-bubble {{ padding: 10px 14px; border-radius: 12px; font-size: .875rem; line-height: 1.55; max-width: 85%; }}
.chat-bubble.assistant {{ background: var(--surface2); border: 1px solid var(--border); border-radius: 4px 12px 12px 12px; align-self: flex-start; }}
.chat-bubble.user {{ background: var(--primary); color: white; border-radius: 12px 4px 12px 12px; align-self: flex-end; }}
@keyframes blink {{ 0%,100%{{opacity:.2}} 50%{{opacity:1}} }}
.thinking-dot {{ display: inline-block; width: 8px; height: 8px; background: var(--text-muted); border-radius: 50%; animation: blink 1s infinite; }}
.chat-input-row {{ display: flex; gap: 8px; margin-top: 4px; }}
.chat-input-row textarea {{ flex: 1; border: 1px solid var(--border-strong); border-radius: 10px; padding: 10px 14px; font-size: .875rem; font-family: inherit; resize: none; line-height: 1.45; max-height: 120px; outline: none; transition: border-color .15s; }}
.chat-input-row textarea:focus {{ border-color: var(--primary); }}
.chat-send {{ background: var(--primary); color: white; border: none; border-radius: 10px; padding: 0 18px; cursor: pointer; font-size: 1.1rem; transition: opacity .15s; }}
.chat-send:disabled {{ opacity: .4; cursor: default; }}
.chat-suggestions {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }}
.chat-chip {{ background: var(--primary-bg); color: var(--primary); border: 1px solid #ddd6fe; border-radius: 20px; padding: 4px 12px; font-size: .78rem; font-weight: 600; cursor: pointer; transition: all .15s; white-space: nowrap; }}
.chat-chip:hover {{ background: var(--primary); color: white; }}
.typing-dot {{ display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--text-muted); margin: 0 1px; animation: blink 1.2s infinite; }}
.typing-dot:nth-child(2) {{ animation-delay: .2s; }}
.typing-dot:nth-child(3) {{ animation-delay: .4s; }}
@keyframes blink {{ 0%,80%,100%{{opacity:.2}} 40%{{opacity:1}} }}

/* CTR & Revenue Badges on Thumbnails */
.ctr-badge {{ display:inline-block; padding:1px 7px; border-radius:10px; font-size:.68rem; font-weight:700; }}
.ctr-great {{ background:#dcfce7; color:#166534; }}
.ctr-good {{ background:#dbeafe; color:#1e40af; }}
.ctr-ok {{ background:#fef9c3; color:#854d0e; }}
.ctr-low {{ background:#fecaca; color:#991b1b; }}
.rev-badge {{ display:inline-block; padding:1px 7px; border-radius:10px; font-size:.68rem; font-weight:700; background:#f0fdf4; color:#166534; }}
.impr-badge {{ display:inline-block; padding:1px 7px; border-radius:10px; font-size:.68rem; font-weight:600; background:#f3f4f6; color:#6b7280; }}

/* Experiment Tracker */
.exp-card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:12px; }}
.exp-card .exp-title {{ font-weight:700; font-size:.9rem; margin-bottom:6px; }}
.exp-card .exp-meta {{ font-size:.78rem; color:var(--text-muted); margin-bottom:8px; }}
.exp-card .exp-hyp {{ font-size:.85rem; padding:10px 14px; background:var(--primary-bg); border-radius:8px; border-left:3px solid var(--primary); }}
.exp-form {{ display:grid; gap:10px; }}
.exp-form input, .exp-form textarea, .exp-form select {{ padding:8px 12px; border:1px solid var(--border); border-radius:8px; font-family:inherit; font-size:.85rem; background:var(--surface); color:var(--text); }}
.exp-form textarea {{ resize:vertical; min-height:60px; }}
.exp-list {{ display:flex; flex-direction:column; gap:10px; margin-top:14px; }}
.score-ring {{ display:inline-flex; align-items:center; justify-content:center; width:52px; height:52px; border-radius:50%; font-weight:800; font-size:1.1rem; }}
.score-high {{ background:#dcfce7; color:#166534; border:3px solid #22c55e; }}
.score-mid {{ background:#fef9c3; color:#854d0e; border:3px solid #eab308; }}
.score-low {{ background:#fecaca; color:#991b1b; border:3px solid #ef4444; }}

/* CSV Upload */
.upload-zone {{ border: 2px dashed var(--border-strong); border-radius: 14px; padding: 32px; text-align: center; cursor: pointer; transition: all .2s; background: var(--surface); }}
.upload-zone:hover, .upload-zone.drag-over {{ border-color: var(--primary); background: var(--primary-bg); }}
.upload-zone .upload-icon {{ font-size: 2.2rem; margin-bottom: 8px; }}
.upload-zone .upload-text {{ font-size: .92rem; color: var(--text-muted); }}
.upload-zone .upload-text strong {{ color: var(--primary); }}
.upload-file-list {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
.upload-file-tag {{ display: inline-flex; align-items: center; gap: 6px; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 5px 12px; font-size: .8rem; color: #166534; font-weight: 600; }}
.upload-file-tag.pending {{ background: #fef9c3; border-color: #fde68a; color: #854d0e; }}
.upload-status {{ margin-top: 14px; padding: 12px 16px; border-radius: 10px; font-size: .85rem; }}
.upload-status.success {{ background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }}
.upload-status.error {{ background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }}
#studio-results {{ display: none; }}
#studio-results.visible {{ display: block; }}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <div class="header-icon">📊</div>
    <div>
      <h1>OKStorytime — YouTube Growth Report</h1>
      <div class="header-sub">Generated {now} &nbsp;·&nbsp; {len(videos):,} videos analyzed &nbsp;·&nbsp; <strong>@OKOPShow</strong></div>
    </div>
  </div>
  <div class="header-stats">
    <div class="hstat"><div class="val">182K</div><div class="lbl">Subscribers</div></div>
    <div class="hstat"><div class="val">{fmt(total_views)}</div><div class="lbl">Total Views</div></div>
    <div class="hstat"><div class="val">{fmt(avg_views)}</div><div class="lbl">Avg / Video</div></div>
    <div class="hstat"><div class="val">{len(videos):,}</div><div class="lbl">Videos</div></div>
    {"" if not has_studio else f'<div class="hstat"><div class="val">{avg_ctr_lf}%</div><div class="lbl">Avg CTR (LF)</div></div>'}
    {"" if not has_studio else f'<div class="hstat"><div class="val">${total_revenue:,.0f}</div><div class="lbl">Total Revenue</div></div>'}
  </div>
</div>

<nav class="nav">
  <button class="nav-btn active" onclick="show('summary',this)">🔑 Summary</button>
  <button class="nav-btn" onclick="show('action',this)">🚀 Action Plan</button>
  <button class="nav-btn" onclick="show('thumbnails',this)">🖼 Thumbnails</button>
  <button class="nav-btn" onclick="show('analytics',this)">📈 Analytics</button>
  <button class="nav-btn" onclick="show('titles',this)">🔤 Titles</button>
  <button class="nav-btn" onclick="show('videos',this)">🎬 Videos</button>
  <button class="nav-btn" onclick="show('competitors',this)">🏆 Competitors</button>
  <button class="nav-btn" onclick="show('shorts',this)">⚡ Shorts Strategy</button>
  <button class="nav-btn" onclick="show('tracker',this)">🚀 Launch Tracker</button>
  <button class="nav-btn" onclick="show('experiments',this)">🧪 Experiments</button>
</nav>


<!-- ════════ SUMMARY ════════ -->
<div id="tab-summary" class="tab active">
  <div class="card" style="margin-top:22px">
    <div class="card-title">🔑 Why You're Losing Viewers — The Short Version</div>
    <div class="two-col" style="margin-top:14px;gap:14px">
      <div>
        <div style="font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--red);margin-bottom:8px">⚠ Problems</div>
        <div class="insight red">❌ <strong>Long-form volume is diluting reach.</strong> Fewer, higher-quality long-form uploads = more views per video. Focus on 3–5 long-form videos/week max.</div>
        <div class="insight red">❌ <strong>Inconsistent thumbnails &amp; titles.</strong> Red studio backdrop, guest faces, and host-first titles are confusing new viewers and lowering CTR.</div>
        <div class="insight red">❌ <strong>New red studio isn't recognized.</strong> Viewers built a strong association with purple/blue + orange. The red backdrop looks like a different channel.</div>
        <div class="insight red">❌ <strong>Guest thumbnails don't convert.</strong> New viewers don't know your guests. Every guest thumbnail tested below average.</div>
      </div>
      <div>
        <div style="font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--green);margin-bottom:8px">✓ Wins</div>
        <div class="insight green">✅ <strong>Long-form is still working.</strong> Long-form averaged 39,629 views in 2024. The format has proven, loyal audience demand.</div>
        <div class="insight green">✅ <strong>Sunday is your superpower.</strong> Sunday averages 46,649 views vs 15,035 on Wednesday — 3.1× difference purely from posting day.</div>
        <div class="insight green">✅ <strong>60–90 min long-form is your sweet spot.</strong> 48,018 avg views — your highest-performing length bracket by far.</div>
        <div class="insight green">✅ <strong>Your podcast is thriving.</strong> Apple Podcasts #75 Comedy in the US. That audience can convert to YouTube viewers.</div>
      </div>
    </div>
  </div>
  <div class="two-col">
    <div class="card">
      <div class="card-title">📉 The Decline Timeline</div>
      <div style="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:12px">
        <button class="dt-tab-btn active" onclick="showDTTab('longform',this)" style="padding:7px 16px;border:none;background:none;font-weight:700;font-size:.85rem;cursor:pointer;border-bottom:2px solid var(--primary);color:var(--primary);font-family:inherit;margin-bottom:-2px">📹 Video</button>
        <button class="dt-tab-btn" onclick="showDTTab('shorts',this)" style="padding:7px 16px;border:none;background:none;font-weight:600;font-size:.85rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit;margin-bottom:-2px">⚡ Shorts</button>
        <button class="dt-tab-btn" onclick="showDTTab('live',this)" style="padding:7px 16px;border:none;background:none;font-weight:600;font-size:.85rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit;margin-bottom:-2px">🔴 Live</button>
      </div>
      <div id="dt-longform" class="dt-panel">
        <div class="table-wrap"><table>
          <tr><th>Period</th><th>Avg Views</th><th>Details</th></tr>
          {decline_html_longform}
        </table></div>
      </div>
      <div id="dt-shorts" class="dt-panel" style="display:none">
        <div class="table-wrap"><table>
          <tr><th>Period</th><th>Avg Views</th><th>Details</th></tr>
          {decline_html_shorts}
        </table></div>
      </div>
      <div id="dt-live" class="dt-panel" style="display:none">
        <div class="table-wrap"><table>
          <tr><th>Period</th><th>Avg Views</th><th>Details</th></tr>
          {decline_html_live}
        </table></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">⚡ Quick Stats</div>
      <div style="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:12px">
        <button class="qs-tab-btn active" onclick="showQSTab('longform',this)" style="padding:7px 16px;border:none;background:none;font-weight:700;font-size:.85rem;cursor:pointer;border-bottom:2px solid var(--primary);color:var(--primary);font-family:inherit;margin-bottom:-2px">📹 Video</button>
        <button class="qs-tab-btn" onclick="showQSTab('shorts',this)" style="padding:7px 16px;border:none;background:none;font-weight:600;font-size:.85rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit;margin-bottom:-2px">⚡ Shorts</button>
        <button class="qs-tab-btn" onclick="showQSTab('live',this)" style="padding:7px 16px;border:none;background:none;font-weight:600;font-size:.85rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit;margin-bottom:-2px">🔴 Live</button>
      </div>
      <div id="qs-longform" class="qs-panel">
        <div class="table-wrap"><table>
          <tr><th>Metric</th><th>Value</th></tr>
          {stats_html_longform}
        </table></div>
      </div>
      <div id="qs-shorts" class="qs-panel" style="display:none">
        <div class="table-wrap"><table>
          <tr><th>Metric</th><th>Value</th></tr>
          {stats_html_shorts}
        </table></div>
      </div>
      <div id="qs-live" class="qs-panel" style="display:none">
        <div class="table-wrap"><table>
          <tr><th>Metric</th><th>Value</th></tr>
          {stats_html_live}
        </table></div>
      </div>
    </div>
  </div>
</div>


<!-- ════════ ACTION PLAN ════════ -->
<div id="tab-action" class="tab">
  <div class="card" style="margin-top:22px">
    <div class="card-title">🚀 5-Step Action Plan to Recover Views</div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 1 — Cut long-form upload volume immediately</strong><br>Target: 3–5 long-form videos/week max. Fewer, higher-quality uploads = more views per video. Every extra upload dilutes your best content's reach. (For Shorts advice, see the ⚡ Shorts Strategy tab.)</div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 2 — Make Sunday your flagship drop</strong><br>Sunday averages 46,649 views — 3.1× better than Wednesday. Best episode of the week goes live Sunday. Try a consistent time: 10am–12pm ET.</div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 3 — Target 60–90 min episodes</strong><br>Your highest-avg format (48,018 views). Structure: 3–4 stories per episode, 15–20 min each, timestamps in description. First-person drama titles on every upload.</div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 4 — Fix the thumbnail formula</strong><br><span class="tag g">Sam or John only</span><span class="tag g">extreme close-up (face fills 80%+)</span><span class="tag g">consistent studio background</span><span class="tag g">mouth open, shocked expression</span><span class="tag g">direct eye contact with camera</span><br><span class="tag r">no guests</span><span class="tag r">no red background</span><span class="tag r">no profile/side shots</span><span class="tag r">no livestream screenshots</span></div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 5 — Lead titles with the story, not the host</strong><br><span class="tag g">Drama-first</span>: "My husband BONED his co-worker" → 1.9M views<br><span class="tag r">Host-first</span>: "Denise reacts to..." → consistently bottom 25%</div>
    <div class="insight yellow"><strong>⚡ Phase 2 — Get Sam's OAuth access for deeper data</strong><br>Unlocks: watch time per video, audience retention curves, CTR per thumbnail, revenue per video. Ask Sam to connect the channel owner Google account. This will give 10× more precise recommendations.</div>
  </div>

  <div class="card">
    <div class="card-title">🎯 10K in 48 Hours — The Launch Gameplan</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 14px">Every video should be treated as a product launch with a 48-hour window. Here's the playbook to hit 10K views within 48 hours of upload.</p>

    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:14px">
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px">
        <div style="font-size:.75rem;font-weight:700;color:var(--primary);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">⏰ Hour 0 — Upload</div>
        <div style="font-size:.82rem;line-height:1.5">
          • Upload with your <strong>best title + thumbnail</strong> — the first version matters most<br>
          • Post at <strong>Sunday 10am–12pm ET</strong> (your best-performing window)<br>
          • Pin a comment immediately with a hook question<br>
          • Share to your community tab + all socials within 15 minutes<br>
          • Reply to the first 10 comments yourself — triggers engagement signal
        </div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px">
        <div style="font-size:.75rem;font-weight:700;color:var(--yellow);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">⏰ Hour 6–12 — Read the Data</div>
        <div style="font-size:.82rem;line-height:1.5">
          • Check impressions vs CTR in YouTube Studio<br>
          • If CTR < 4%: <strong>swap the thumbnail</strong> — YouTube re-tests with new art<br>
          • If CTR > 6% but views are low: title isn't hooking browse — <strong>rewrite the title</strong><br>
          • If both are good but views still low: impressions are low — promote harder on socials<br>
          • Post a TikTok clip driving to the full video
        </div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px">
        <div style="font-size:.75rem;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">⏰ Hour 24–48 — Double Down or Pivot</div>
        <div style="font-size:.82rem;line-height:1.5">
          • If already at 5K+: ride the wave — post a follow-up Short with a cliffhanger from the video<br>
          • If under 3K: <strong>swap both title AND thumbnail</strong> — treat it as a re-launch<br>
          • Post a 2nd TikTok clip (different moment)<br>
          • Cross-promote in the next video's intro: "If you missed yesterday's…"<br>
          • After 48 hours, stop tweaking — algorithm has decided
        </div>
      </div>
    </div>

    <div class="insight green" style="margin-bottom:8px"><strong>The #1 lever you don't have yet: CTR data.</strong> Once you get the OAuth token, we can tell you EXACTLY which thumbnails are underperforming and need swapping. Right now you're guessing. With CTR data, you'll know within 2 hours if a thumbnail is working.</div>
    <div class="insight yellow" style="margin-bottom:8px"><strong>Editing for retention:</strong> The first 30 seconds decide everything. Start mid-story (cold open), no intros, no "hey guys." Cut to the most shocking moment of the story immediately. Add text overlays for key reveals. Use jump cuts every 3–5 seconds during narration to keep visual movement. End every video with an unresolved tease for the next one.</div>
    <div class="insight yellow"><strong>Your current editing gap:</strong> Livestream VODs have no editing — that's why they underperform. Pre-recorded episodes with cuts, zooms, and reaction inserts get 2–4x the views of raw livestream recordings. Invest editing time in your Sunday flagship, not in more uploads.</div>
  </div>

  <div class="card">
    <div class="card-title">📊 Title Pattern: First-Person vs Generic — What the Data Shows</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 12px">First-person drama titles consistently outperform generic or third-person framing across the channel's entire history.</p>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px">
      <div>
        <div style="font-size:.75rem;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">First-Person Titles That Win</div>
        <div class="insight green" style="margin-bottom:6px;font-size:.82rem">My Husband BONED his Co-Worker… You Won't Believe — <strong>1.9M</strong></div>
        <div class="insight green" style="margin-bottom:6px;font-size:.82rem">He "did it" with BOTH HER BOYFRIENDS — <strong>203K</strong></div>
        <div class="insight green" style="margin-bottom:6px;font-size:.82rem">Blackmailing my Stepdad… With His TINDER? — <strong>160K</strong></div>
        <p style="font-size:.8rem;color:var(--text-muted);margin-top:8px"><strong>Pattern:</strong> First-person shock statement, specific dramatic action, implies a twist, unresolved tension</p>
      </div>
      <div>
        <div style="font-size:.75rem;font-weight:700;color:var(--red);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Generic/Vague Titles That Flop</div>
        <div class="insight red" style="margin-bottom:6px;font-size:.82rem">Leave the animals alone! — <strong>11K</strong></div>
        <div class="insight red" style="margin-bottom:6px;font-size:.82rem">Friends that are as real as the Nigerian Prince… — <strong>16K</strong></div>
        <div class="insight red" style="margin-bottom:6px;font-size:.82rem">when grandma's will causes world war III — <strong>17K</strong></div>
        <p style="font-size:.8rem;color:var(--text-muted);margin-top:8px"><strong>Pattern:</strong> Generic category labels, no specific story hook, no first-person voice, tries to be clever instead of dramatic</p>
      </div>
    </div>

    <div class="insight yellow" style="margin-top:12px"><strong>The rule:</strong> Every title should lead with the most dramatic moment from the story in first-person: "My boss demanded I work on my wedding day… so I quit live on Zoom." Specific beats vague. Drama beats clever. First-person ("My/I") beats third-person ("She/He") by a wide margin.</div>
  </div>

  <div class="card">
    <div class="card-title">🎬 The First 30 Seconds — Intro Formula (Transcript Analysis)</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 14px">We analyzed transcripts of the top 10 vs median-performing long-form videos. <strong>Word count is identical</strong> (~96 words in 30s). The difference is entirely in WHAT you say, not how much.</p>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px">
      <div style="background:var(--surface);border:2px solid var(--green);border-radius:10px;padding:16px">
        <div style="font-size:.75rem;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px">✅ Top Videos (148K–212K views) — What They Do</div>
        <div style="font-size:.83rem;line-height:1.7">
          <strong>1. Double-Twist Cold Open</strong> — Every single top video opens with a 1-2 sentence dramatic summary containing the conflict AND an unexpected reversal:<br>
          <div style="background:var(--surface2);border-radius:8px;padding:10px;margin:8px 0;font-style:italic;font-size:.8rem;line-height:1.6">
            "my husband thinks I'm ugly so he wants an open marriage — I told him the first guy I want to see and now he doesn't want one anymore" — <strong>148K</strong><br><br>
            "she cheated on me so I had a threesome with her ex-boyfriend and the male mistress but now I think I'm in love with both of them" — <strong>202K</strong><br><br>
            "I'm infertile so my husband boned his co-worker... they just took a paternity test and I can't believe the results" — <strong>173K</strong>
          </div>
          <strong>2. Under 5 seconds to story</strong> — No channel plugs, no "hey guys," no housekeeping before the hook<br>
          <strong>3. Taboo subject first</strong> — Paternity, infidelity, financial crimes by family, infertility<br>
          <strong>4. Host reactions: 1–5 words max</strong> — "yikes," "oh my god," "dang" — never derails momentum<br>
          <strong>5. Channel plugs after 60 seconds</strong> — Never in the first 30 seconds
        </div>
      </div>
      <div style="background:var(--surface);border:2px solid var(--red);border-radius:10px;padding:16px">
        <div style="font-size:.75rem;font-weight:700;color:var(--red);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px">❌ Median Videos (~21K views) — What They Do Wrong</div>
        <div style="font-size:.83rem;line-height:1.7">
          <strong>1. Single conflict, no twist</strong> — "my best friend turned out to be the worst roommate." Direct, but no "wait, WHAT?" factor<br><br>
          <strong>2. Housekeeping in the first 10 seconds</strong> — "if you want to submit your own stories, go to r/okstorytime" — kills momentum before the hook<br><br>
          <strong>3. Less taboo topics</strong> — Bad roommates, deadbeat dads, cult parents, wedding pranks — relatable but not shocking<br><br>
          <strong>4. Longer host commentary up front</strong> — Extended riffing and conversational tone slows the hook<br><br>
          <strong>5. No reversal or cliffhanger</strong> — Viewers can predict the story from the opening
        </div>
      </div>
    </div>

    <div style="background:linear-gradient(135deg,var(--surface) 0%,var(--surface2) 100%);border:2px solid var(--primary);border-radius:12px;padding:18px;margin-bottom:14px">
      <div style="font-size:.9rem;font-weight:700;color:var(--primary);margin-bottom:10px">🎯 THE FORMULA — Use This for Every Video</div>
      <div style="font-size:.95rem;line-height:1.8;font-weight:600;text-align:center;padding:8px 0">
        "[Shocking situation] + but/so [unexpected consequence] + and now [ironic reversal]"
      </div>
      <div style="font-size:.82rem;color:var(--text-muted);text-align:center;margin-top:4px">All within the first sentence. Before anything else. No exceptions.</div>
    </div>

    <div class="insight green" style="margin-bottom:8px"><strong>Title Generator Rule:</strong> Take the best story's opening hook and use it as the title. If the hook has a double-twist, include BOTH twists. "My husband boned his co-worker — I can't believe the paternity test results" beats "Wild paternity test stories" every time.</div>
    <div class="insight yellow"><strong>Team action item:</strong> Print this formula and tape it to the editing station. Before recording any video, write the cold-open sentence first. If it doesn't have a twist AND a reversal, rewrite it.</div>
  </div>

  <div class="card" id="transcript-deep-dive">
    <div class="card-title">🔬 Deep Transcript Analysis — Top 50 vs Bottom 50</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 12px">Full analysis of 100 video transcripts comparing what top performers do differently. Loading from latest analysis...</p>
    <div id="transcript-analysis-content">
      {transcript_deep_html if transcript_deep_html else '<p style="color:var(--text-muted);font-style:italic">Run deep_transcript_analysis.py to generate the full analysis. It will appear here on next report build.</p>'}
    </div>
  </div>

  <div class="card">
    <div class="card-title">📋 Weekly Content Schedule Template</div>
    <div class="table-wrap"><table>
      <tr><th>Day</th><th>Content</th><th>Format</th><th>Why</th></tr>
      <tr class="highlight-row"><td><strong>Sunday ⭐</strong></td><td>Flagship long-form episode</td><td>60–90 min</td><td>Best day (46,649 avg) + best format (48,018 avg)</td></tr>
      <tr><td>Tuesday</td><td>Single story deep-dive</td><td>40–60 min</td><td>36,157 avg for this length</td></tr>
      <tr><td>Thursday</td><td>Hot take / audience debate</td><td>40–60 min</td><td>Good mid-week, 19,575 avg Thursday</td></tr>
      <tr><td>Optional</td><td>1 Short — only if genuinely viral-worthy</td><td>&lt;60 sec</td><td>De-prioritize for now</td></tr>
    </table></div>
    <p style="font-size:.82rem;color:var(--text-muted);margin-top:12px">💡 3 quality videos/week instead of 70+ will likely <em>increase</em> your total views based on the data.</p>
  </div>

  <!-- Comeback Roadmap -->
  <div class="card">
    <div class="card-title">📈 Comeback Roadmap — What Actually Works (Based on Real Cases)</div>
    <p style="font-size:.84rem;color:var(--text-muted);margin:0 0 16px">Channels that crashed and recovered all followed a similar pattern. Here's what they did and how it maps to OKStorytime.</p>

    <div class="two-col" style="gap:14px;margin-bottom:18px">
      <div class="card" style="background:var(--surface2);border:1px solid var(--border);margin:0;padding:16px">
        <div style="font-weight:700;font-size:.9rem;margin-bottom:10px">🔄 Smosh — Crashed, rebuilt, came back</div>
        <div style="font-size:.83rem;color:var(--text-muted);line-height:1.6">
          Sold the channel, company went bankrupt, original founders bought it back with zero staff. Took <strong>3 years</strong> of consistent posting to recover to pre-crash subs.<br><br>
          <span style="color:var(--green)">✓ What worked:</span> Returned to the original format viewers loved. Stopped chasing trends. Made their comeback part of the story — audience rooted for them.<br><br>
          <span style="color:var(--yellow)">→ OKStorytime parallel:</span> Your original purple studio + long-form reaction format is the "original Smosh." Return to it explicitly.
        </div>
      </div>
      <div class="card" style="background:var(--surface2);border:1px solid var(--border);margin:0;padding:16px">
        <div style="font-weight:700;font-size:.9rem;margin-bottom:10px">📉 Philip DeFranco — Burned out, came back stronger</div>
        <div style="font-size:.83rem;color:var(--text-muted);line-height:1.6">
          Uploaded daily for years → views collapsed → took 6-month break → returned with 3x/week schedule and tighter format. Views <strong>tripled</strong> within 4 months of return.<br><br>
          <span style="color:var(--green)">✓ What worked:</span> Publicly explained the change. "We're doing fewer, better videos." Audience respected the honesty and came back.<br><br>
          <span style="color:var(--yellow)">→ OKStorytime parallel:</span> A "here's what's changing" video could convert passive subscribers into active viewers again.
        </div>
      </div>
    </div>

    <div class="two-col" style="gap:14px;margin-bottom:18px">
      <div class="card" style="background:var(--surface2);border:1px solid var(--border);margin:0;padding:16px">
        <div style="font-weight:700;font-size:.9rem;margin-bottom:10px">🎮 Markiplier — Channel struck, rebuilt from zero</div>
        <div style="font-size:.83rem;color:var(--text-muted);line-height:1.6">
          Lost his original channel entirely due to a false strike. Started from scratch on a new channel with no subscribers. Hit 1M in under a year.<br><br>
          <span style="color:var(--green)">✓ What worked:</span> Didn't try to recreate the old channel. Leaned into his personality and let the format evolve naturally. Consistency over perfection.<br><br>
          <span style="color:var(--yellow)">→ OKStorytime parallel:</span> The audience is still there (1.5M subs). The problem is content strategy, not brand loyalty.
        </div>
      </div>
      <div class="card" style="background:var(--surface2);border:1px solid var(--border);margin:0;padding:16px">
        <div style="font-weight:700;font-size:.9rem;margin-bottom:10px">🎙️ Two Hot Takes — Podcast → YouTube cross-promotion</div>
        <div style="font-size:.83rem;color:var(--text-muted);line-height:1.6">
          Started as a podcast, used YouTube as a distribution channel. iHeart distribution + TikTok clips → 875K YouTube subs and ~150K avg views per video.<br><br>
          <span style="color:var(--green)">✓ What worked:</span> Treated each platform differently. Podcast listeners ≠ YouTube viewers. Tailored content to each.<br><br>
          <span style="color:var(--yellow)">→ OKStorytime parallel:</span> The podcast is at Apple #75 Comedy. That audience doesn't automatically watch YouTube. A direct "come watch us on YouTube" push could be a quick win.
        </div>
      </div>
    </div>

    <div class="insight yellow" style="margin-top:4px">
      <strong>The universal pattern across every comeback:</strong> Cut volume → pick one flagship format → be consistent for 90 days → make the change public so your existing audience knows to re-engage.
      Based on OKStorytime's data, the target is <strong>Sunday 60–90 min flagship episodes, 3 videos/week max, purple studio, Sam or John only, first-person drama titles</strong>. Give it 90 days of strict adherence before evaluating.
    </div>
  </div>
</div>


<!-- ════════ THUMBNAILS ════════ -->
<div id="tab-thumbnails" class="tab">
  <div class="card" style="margin-top:22px">
    <div class="card-title">🎯 The Winning Thumbnail Formula</div>
    <div class="formula-grid">
      <div class="formula do">
        <div class="formula-title">✅ DO THIS</div>
        <ul>
          <li><span class="icon">👤</span><div><span class="lbl">Host:</span><span class="desc">Sam or John only — no guests</span></div></li>
          <li><span class="icon">🔍</span><div><span class="lbl">Shot:</span><span class="desc">Extreme close-up — face fills 80% of frame</span></div></li>
          <li><span class="icon">🎨</span><div><span class="lbl">Background:</span><span class="desc">Purple or blue studio only</span></div></li>
          <li><span class="icon">🎯</span><div><span class="lbl">Consistency:</span><span class="desc">Same studio setup every time — builds instant recognition</span></div></li>
          <li><span class="icon">😱</span><div><span class="lbl">Expression:</span><span class="desc">Mouth open — shocked, laughing, disbelief</span></div></li>
          <li><span class="icon">👀</span><div><span class="lbl">Eyes:</span><span class="desc">Wide open, staring directly at camera</span></div></li>
          <li><span class="icon">🔤</span><div><span class="lbl">Text:</span><span class="desc">Optional small caption at top only</span></div></li>
          <li><span class="icon">✨</span><div><span class="lbl">Layout:</span><span class="desc">Clean — zero clutter</span></div></li>
        </ul>
      </div>
      <div class="formula dont">
        <div class="formula-title">❌ STOP DOING THIS</div>
        <ul>
          <li><span class="icon">🚫</span><div><span class="lbl">Guests</span><span class="desc"> — viewers don't know them, won't click</span></div></li>
          <li><span class="icon">🚫</span><div><span class="lbl">Red background</span><span class="desc"> — looks like a different channel</span></div></li>
          <li><span class="icon">🚫</span><div><span class="lbl">Profile/side shots</span><span class="desc"> — no eye contact = no click</span></div></li>
          <li><span class="icon">🚫</span><div><span class="lbl">Eyes closed</span><span class="desc"> or looking down</span></div></li>
          <li><span class="icon">🚫</span><div><span class="lbl">Livestream screenshots</span><span class="desc"> with UI overlays</span></div></li>
          <li><span class="icon">🚫</span><div><span class="lbl">Outdoor shots</span><span class="desc"> — no studio identity</span></div></li>
          <li><span class="icon">🚫</span><div><span class="lbl">Stock/AI images</span><span class="desc"> (Santa, meme photos)</span></div></li>
          <li><span class="icon">🚫</span><div><span class="lbl">Host name bars</span><span class="desc"> ("SAM") as the main hook</span></div></li>
        </ul>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">🏆 Top Performing Long-Form Thumbnails — Study These</div>
    <p style="font-size:.84rem;color:var(--text-muted);margin-bottom:8px">True long-form only (5+ min horizontal). CTR badge: <span class="ctr-badge ctr-great">12%+ Banger</span> <span class="ctr-badge ctr-good">8-12% Good</span> <span class="ctr-badge ctr-ok">4-8% OK</span> <span class="ctr-badge ctr-low">&lt;4% Weak</span> &nbsp;·&nbsp; <strong>24h</strong> = first 24-hour CTR (via API) &nbsp;·&nbsp; <strong>LT</strong> = lifetime CTR (Studio export)</p>
    <p style="font-size:.78rem;color:var(--text-muted);margin-bottom:12px">💡 <strong>Sam's rule:</strong> 12%+ CTR in the first hour = banger. Get first-24h data by having Sam run <code style="background:#f5f3ff;padding:1px 5px;border-radius:4px;color:var(--primary)">auth_youtube.py</code> once, then <code style="background:#f5f3ff;padding:1px 5px;border-radius:4px;color:var(--primary)">fetch_analytics.py</code>.</p>
    <div id="thumb-filters" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <button class="filter-btn active" onclick="filterThumbs('all',this)">Lifetime</button>
      {thumb_year_btns}
      <button class="filter-btn" onclick="filterThumbs('last30',this)">Last 30 Days</button>
      <button class="filter-btn" onclick="filterThumbs('last7',this)">Last 7 Days</button>
    </div>
    <div id="thumb-sort" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
      <span style="font-size:.82rem;color:var(--text-muted);font-weight:600;padding:5px 0">Sort:</span>
      <button class="filter-btn active" onclick="setThumbSort('views',this)">Most Views</button>
      <button class="filter-btn" onclick="setThumbSort('ctr',this)">Highest CTR</button>
      <button class="filter-btn" onclick="setThumbSort('revenue',this)">Most Revenue</button>
    </div>
    <div id="top-thumb-grid"></div>
  </div>
  <div class="card">
    <div class="card-title">⚠️ Your Lowest Performing Long-Form Thumbnails — Avoid These Patterns</div>
    <p style="font-size:.84rem;color:var(--text-muted);margin-bottom:4px">True long-form (5+ min) 2024+ videos with fewest views · red backgrounds, guests, profile shots, cluttered UI, low energy.</p>
    {bot_grid}
  </div>

  <div class="card">
    <div class="card-title">🏆 Competitor Thumbnails — Reddit Story Niche</div>
    <p style="font-size:.84rem;color:var(--text-muted);margin-bottom:12px">Long-form only (Shorts filtered out). Sorted by views. History builds daily — the longer this runs, the more data you see.</p>
    <div id="comp-thumb-filters" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
      <button class="filter-btn active" onclick="filterCompThumbs('week',this)">Last 7 Days</button>
      <button class="filter-btn" onclick="filterCompThumbs('month',this)">Last 30 Days</button>
      <button class="filter-btn" onclick="filterCompThumbs('year',this)">Last Year</button>
      <button class="filter-btn" onclick="filterCompThumbs('all',this)">All Time</button>
    </div>
    <div id="comp-thumb-grid"></div>
    <button id="comp-thumb-more" onclick="showMoreCompThumbs()" style="display:none;margin-top:12px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:8px 20px;font-size:.85rem;font-weight:600;cursor:pointer;color:var(--primary);width:100%">Show 25 More</button>
  </div>

  <!-- AI Thumbnail Maker -->
  <div class="card" id="thumb-workshop">
    <div class="card-title">🎨 AI Thumbnail Maker</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin:0 0 14px">Paste your story, select who's in the episode, and get 3 AI-generated thumbnail concepts with titles. Each concept targets a different strategy: your proven style, competitor-inspired, and experimental.</p>

    <!-- Host Photo Library -->
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <div style="font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted)">Host Photo Library</div>
        <button onclick="document.getElementById('host-photo-upload').click()" style="padding:6px 14px;background:var(--primary);color:#fff;border:none;border-radius:8px;font-size:.78rem;font-weight:600;cursor:pointer;font-family:inherit">+ Add Photos</button>
        <input type="file" id="host-photo-upload" accept="image/*" multiple style="display:none" onchange="handleHostPhotoUpload(this.files)">
      </div>
      <div id="host-photos-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:10px;min-height:60px">
        <div id="host-photos-empty" style="grid-column:1/-1;text-align:center;padding:20px;color:var(--text-muted);font-size:.82rem;border:2px dashed var(--border);border-radius:10px">
          No host photos yet. Upload PNG/JPG photos of Sam, John, or other hosts.<br>
          <span style="font-size:.75rem">Tip: transparent background PNGs work best for compositing</span>
        </div>
      </div>
    </div>

    <!-- Story + Settings -->
    <div style="display:grid;grid-template-columns:1fr 240px;gap:14px;margin-bottom:14px">
      <div>
        <label style="font-size:.78rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:6px">Story / Topic</label>
        <textarea id="thumb-story-input" rows="5" placeholder="Paste the story summary here... e.g. 'A wife finds out her husband has been secretly sending money to his ex for 3 years. When she confronts him, his mom takes his side.'"
          style="width:100%;padding:12px 14px;border:1px solid var(--border);border-radius:10px;font-family:inherit;font-size:.875rem;line-height:1.55;resize:vertical;background:var(--surface2);color:var(--text);outline:none;box-sizing:border-box"></textarea>
      </div>
      <div style="display:flex;flex-direction:column;gap:12px">
        <div>
          <label style="font-size:.78rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:6px">Who's in this episode?</label>
          <select id="thumb-host" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:8px;font-family:inherit;font-size:.85rem;background:var(--surface2);color:var(--text)">
            <option value="sam">Sam (solo)</option>
            <option value="john">John (solo)</option>
            <option value="both">Sam + John</option>
            <option value="guest">Guest episode</option>
          </select>
        </div>
        <div>
          <label style="font-size:.78rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:6px">Episode length</label>
          <select id="thumb-length" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:8px;font-family:inherit;font-size:.85rem;background:var(--surface2);color:var(--text)">
            <option value="60-90">60-90 min (best)</option>
            <option value="40-60">40-60 min</option>
            <option value="20-40">20-40 min</option>
            <option value="10-20">10-20 min</option>
          </select>
        </div>
        <div>
          <label style="font-size:.78rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:6px">Text overlay style</label>
          <select id="thumb-text-style" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:8px;font-family:inherit;font-size:.85rem;background:var(--surface2);color:var(--text)">
            <option value="bold-keyword">Bold keyword (1-2 words, e.g. "EXPOSED")</option>
            <option value="short-quote">Short quote from story</option>
            <option value="none">No text overlay</option>
          </select>
        </div>
      </div>
    </div>

    <!-- Generate Button -->
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:14px">
      <button id="thumb-gen-btn" onclick="generateThumbnails()" style="background:linear-gradient(135deg,var(--primary),#7c3aed);color:#fff;border:none;border-radius:10px;padding:12px 28px;font-size:.9rem;font-weight:700;cursor:pointer;font-family:inherit;box-shadow:0 2px 8px rgba(124,58,237,.3)">Generate 3 Thumbnails</button>
      <span id="thumb-gen-status" style="font-size:.82rem;color:var(--text-muted)"></span>
    </div>

    <!-- Results: 3 thumbnail concepts -->
    <div id="thumb-results" style="display:none">
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:18px">
        <!-- Concept 1: Your Style -->
        <div style="border:2px solid var(--green);border-radius:12px;overflow:hidden;background:var(--surface)">
          <div style="background:var(--green-bg);padding:10px 14px;font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#065f46;display:flex;align-items:center;gap:6px">
            <span>1</span> Your Proven Style
          </div>
          <div id="thumb-concept-1" style="padding:14px">
            <div id="thumb-img-1" style="aspect-ratio:16/9;background:var(--surface2);border-radius:8px;margin-bottom:10px;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative">
              <span style="color:var(--text-muted);font-size:.8rem">Thumbnail will appear here</span>
            </div>
            <div id="thumb-title-1" style="font-size:.9rem;font-weight:700;line-height:1.4;margin-bottom:6px"></div>
            <div id="thumb-why-1" style="font-size:.78rem;color:var(--text-muted);line-height:1.4"></div>
          </div>
        </div>
        <!-- Concept 2: Competitor-Inspired -->
        <div style="border:2px solid var(--primary);border-radius:12px;overflow:hidden;background:var(--surface)">
          <div style="background:var(--primary-bg);padding:10px 14px;font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--primary);display:flex;align-items:center;gap:6px">
            <span>2</span> Competitor-Inspired
          </div>
          <div id="thumb-concept-2" style="padding:14px">
            <div id="thumb-img-2" style="aspect-ratio:16/9;background:var(--surface2);border-radius:8px;margin-bottom:10px;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative">
              <span style="color:var(--text-muted);font-size:.8rem">Thumbnail will appear here</span>
            </div>
            <div id="thumb-title-2" style="font-size:.9rem;font-weight:700;line-height:1.4;margin-bottom:6px"></div>
            <div id="thumb-why-2" style="font-size:.78rem;color:var(--text-muted);line-height:1.4"></div>
          </div>
        </div>
        <!-- Concept 3: Experimental -->
        <div style="border:2px solid #f59e0b;border-radius:12px;overflow:hidden;background:var(--surface)">
          <div style="background:#fffbeb;padding:10px 14px;font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#92400e;display:flex;align-items:center;gap:6px">
            <span>3</span> Experimental / Fresh
          </div>
          <div id="thumb-concept-3" style="padding:14px">
            <div id="thumb-img-3" style="aspect-ratio:16/9;background:var(--surface2);border-radius:8px;margin-bottom:10px;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative">
              <span style="color:var(--text-muted);font-size:.8rem">Thumbnail will appear here</span>
            </div>
            <div id="thumb-title-3" style="font-size:.9rem;font-weight:700;line-height:1.4;margin-bottom:6px"></div>
            <div id="thumb-why-3" style="font-size:.78rem;color:var(--text-muted);line-height:1.4"></div>
          </div>
        </div>
      </div>

      <!-- Refinement Chat -->
      <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:14px">
        <div style="font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:10px">Refine Your Thumbnails</div>
        <div id="thumb-chat-messages" style="min-height:60px;max-height:280px;overflow-y:auto;display:flex;flex-direction:column;gap:10px;margin-bottom:10px">
          <div class="chat-bubble assistant" style="font-size:.82rem">Pick a concept above and tell me what to change. E.g. "Make #1 more dramatic", "Try #2 with angry expression", "Add 'EXPOSED' text to #3"</div>
        </div>
        <div style="display:flex;gap:8px">
          <input type="text" id="thumb-chat-input" placeholder="e.g. 'Make concept 2 more dramatic, add red glow behind the text...'" style="flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:10px;background:var(--surface);color:var(--text);font-size:.85rem;font-family:inherit" onkeydown="if(event.key==='Enter')refineThumb()">
          <button onclick="refineThumb()" style="padding:10px 18px;background:var(--primary);color:#fff;border:none;border-radius:10px;font-weight:700;cursor:pointer;font-family:inherit;font-size:.85rem">Refine</button>
        </div>
      </div>
    </div>

    <!-- OpenAI Key (for DALL-E) -->
    <div style="margin-top:14px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:12px 16px">
      <div style="display:flex;gap:8px;align-items:center" id="openai-key-bar">
        <span style="font-size:.82rem;color:var(--text-muted);font-weight:600;white-space:nowrap">OpenAI API Key (for DALL-E images):</span>
        <input type="password" id="openai-key-input" placeholder="sk-... (stored locally, never uploaded)" style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:8px;font-size:.82rem;font-family:inherit;background:var(--surface);color:var(--text)">
        <button onclick="saveOpenAIKey()" style="padding:6px 14px;background:var(--primary);color:#fff;border:none;border-radius:8px;font-size:.82rem;font-weight:600;cursor:pointer;font-family:inherit">Save</button>
      </div>
      <div id="openai-key-saved" style="display:none;align-items:center;gap:8px;font-size:.82rem;color:var(--green)">
        <span>OpenAI key saved locally</span>
        <button onclick="clearOpenAIKey()" style="padding:4px 10px;background:var(--surface);color:var(--text-muted);border:1px solid var(--border);border-radius:6px;font-size:.78rem;cursor:pointer;font-family:inherit">Change Key</button>
      </div>
      <p style="font-size:.75rem;color:var(--text-muted);margin:8px 0 0">Without OpenAI key: generates text descriptions + composites host photos. With key: generates full AI thumbnail images via DALL-E 3 (~$0.04 each). Also needs Anthropic key (Analytics tab) for title/concept generation.</p>
    </div>
  </div>

</div>


<!-- ════════ ANALYTICS ════════ -->
<div id="tab-analytics" class="tab">

  <!-- CSV Upload -->
  <div class="card" style="margin-top:22px">
    <div class="card-title">📤 Upload YouTube Studio Data</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:14px">
      Export from <strong>YouTube Studio > Analytics > Advanced Mode > Download</strong>, then drop the CSV files here.
      Data is processed in your browser — nothing is uploaded to any server.
    </p>
    <div class="upload-zone" id="upload-zone"
         ondragover="event.preventDefault();this.classList.add('drag-over')"
         ondragleave="this.classList.remove('drag-over')"
         ondrop="event.preventDefault();this.classList.remove('drag-over');handleCSVDrop(event.dataTransfer.files)"
         onclick="document.getElementById('csv-file-input').click()">
      <div class="upload-icon">📁</div>
      <div class="upload-text">
        <strong>Drop CSV files here</strong> or click to browse<br>
        <span style="font-size:.78rem">Accepts: Table data.csv, Chart data.csv, Totals.csv</span>
      </div>
      <input type="file" id="csv-file-input" multiple accept=".csv,.CSV" style="display:none"
             onchange="handleCSVDrop(this.files)">
    </div>
    <div class="upload-file-list" id="upload-file-list"></div>
    <div id="upload-status"></div>
  </div>

  <!-- Studio results (populated by JS after upload) -->
  <div id="studio-results">
    <div class="card">
      <div class="card-title">💰 Studio Analytics — Top Long-Form by Revenue</div>
      <div id="studio-summary-line" style="font-size:.82rem;color:var(--text-muted);margin-bottom:12px"></div>
      <div class="table-wrap"><table id="studio-table">
        <tr><th>Title</th><th>Views</th><th>Impressions</th><th>CTR</th><th>Avg Duration</th><th>Avg % Viewed</th><th>Revenue</th><th>RPM</th></tr>
      </table></div>
    </div>
    <div class="two-col">
      <div class="card">
        <div class="card-title">🎯 Views & Revenue by CTR Bucket (Long-Form)</div>
        <div class="table-wrap"><table id="studio-ctr-table">
          <tr><th>CTR Range</th><th>Videos</th><th>Avg Views</th><th>Avg Revenue</th></tr>
        </table></div>
      </div>
      <div class="card">
        <div class="card-title">📊 Key Studio Metrics (Long-Form)</div>
        <div class="table-wrap"><table id="studio-metrics-table"></table></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">📈 Monthly Channel Engaged Views</div>
      <div class="chart-container"><canvas id="studio-monthly-chart"></canvas></div>
    </div>
  </div>

  <!-- Day / Length filter -->
  <div class="filter-bar" style="margin-top:22px">
    <span>Filter tables by year:</span>
    <button class="filter-btn active" id="fb-all" onclick="filterAnalytics('all',this)">All Time</button>
    <button class="filter-btn" onclick="filterAnalytics('2023',this)">2023</button>
    <button class="filter-btn" onclick="filterAnalytics('2024',this)">2024</button>
    <button class="filter-btn" onclick="filterAnalytics('2025',this)">2025</button>
    <button class="filter-btn" onclick="filterAnalytics('2026',this)">2026</button>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="card-title">📅 Avg Views by Day of Week (Long-Form)</div>
      <div class="table-wrap">
      <table id="day-table">
        <tr><th>Day</th><th>Avg Views</th><th>Videos</th><th></th></tr>
      </table>
      </div>
      <p id="day-note" style="font-size:.82rem;color:var(--text-muted);margin-top:10px"></p>
    </div>
    <div class="card">
      <div class="card-title">⏱️ Avg Views by Video Length (Long-Form)</div>
      <div class="table-wrap">
      <table id="len-table">
        <tr><th>Length</th><th>Avg Views</th><th>Videos</th><th></th></tr>
      </table>
      </div>
      <p id="len-note" style="font-size:.82rem;color:var(--text-muted);margin-top:10px"></p>
    </div>
  </div>

  <!-- Monthly Trend Chart — YouTube Studio Style -->
  <div class="card" id="trend-card">
    <!-- Metric Tabs -->
    <div style="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:16px;overflow-x:auto" id="metric-tabs">
      <button class="metric-tab active" onclick="setMetric('views',this)">
        <div class="metric-tab-value" id="mt-views-val">—</div>
        <div class="metric-tab-label">Views</div>
      </button>
      <button class="metric-tab" onclick="setMetric('watch_hours',this)">
        <div class="metric-tab-value" id="mt-watch_hours-val">—</div>
        <div class="metric-tab-label">Watch time (hrs)</div>
      </button>
      <button class="metric-tab" onclick="setMetric('avg_duration',this)">
        <div class="metric-tab-value" id="mt-avg_duration-val">—</div>
        <div class="metric-tab-label">Avg duration</div>
      </button>
      <button class="metric-tab" onclick="setMetric('ctr',this)">
        <div class="metric-tab-value" id="mt-ctr-val">—</div>
        <div class="metric-tab-label">CTR</div>
      </button>
      <button class="metric-tab" onclick="setMetric('revenue',this)">
        <div class="metric-tab-value" id="mt-revenue-val">—</div>
        <div class="metric-tab-label">Revenue</div>
      </button>
    </div>
    <!-- Time Range Filters -->
    <div class="filter-bar" id="chart-filters" style="margin-bottom:12px">
      <button class="filter-btn" onclick="setChartRange(3,this)">3M</button>
      <button class="filter-btn" onclick="setChartRange(6,this)">6M</button>
      <button class="filter-btn active" onclick="setChartRange(12,this)">1Y</button>
      <button class="filter-btn" onclick="setChartRange(24,this)">2Y</button>
      <button class="filter-btn" onclick="setChartRange(0,this)">All Time</button>
    </div>
    <div class="chart-container" style="height:320px">
      <canvas id="monthly-chart"></canvas>
    </div>
    <div class="chart-meta" id="chart-meta"></div>
    <p style="font-size:.78rem;color:var(--text-muted);margin-top:12px">
      Long-form videos only (5+ min). <strong>Auto-update:</strong> Re-run <code style="background:#f5f3ff;padding:1px 5px;border-radius:4px;color:var(--primary)">fetch_channel_data.py</code> then <code style="background:#f5f3ff;padding:1px 5px;border-radius:4px;color:var(--primary)">generate_report.py</code> to refresh.
    </p>
  </div>

  {"" if not has_studio else f"""
  <!-- Studio Analytics: Revenue & CTR -->
  <div class="card">
    <div class="card-title">💰 Studio Analytics — Top Long-Form by Revenue</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:12px">
      Data from YouTube Studio export. {len(lf_with_ctr)} long-form videos with CTR data.
      Avg CTR: <strong>{avg_ctr_lf}%</strong> &middot;
      Total Revenue: <strong>${total_revenue:,.0f}</strong> &middot;
      Total Impressions: <strong>{total_impressions:,}</strong> &middot;
      Total Watch Hours: <strong>{total_watch_hours:,.0f}</strong>
    </p>
    <div class="table-wrap"><table>
      <tr><th>Title</th><th>Views</th><th>Impressions</th><th>CTR</th><th>Avg Duration</th><th>Avg % Viewed</th><th>Revenue</th><th>RPM</th></tr>
      {studio_table_rows}
    </table></div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="card-title">🎯 Views & Revenue by CTR Bucket (Long-Form)</div>
      <div class="table-wrap"><table>
        <tr><th>CTR Range</th><th>Videos</th><th>Avg Views</th><th>Avg Revenue</th></tr>
        {ctr_bucket_rows}
      </table></div>
    </div>
    <div class="card">
      <div class="card-title">📊 Key Studio Metrics (Long-Form)</div>
      <div class="table-wrap"><table>
        <tr><td>Avg CTR (long-form)</td><td class="num">{avg_ctr_lf}%</td></tr>
        <tr><td>Total Revenue</td><td class="num">${total_revenue:,.0f}</td></tr>
        <tr><td>Total Impressions</td><td class="num">{total_impressions:,}</td></tr>
        <tr><td>Total Watch Hours</td><td class="num">{total_watch_hours:,.0f}</td></tr>
        <tr><td>Avg Revenue / Video</td><td class="num">${total_revenue / max(len(lf_vids), 1):,.0f}</td></tr>
        <tr><td>Avg RPM (long-form)</td><td class="num">${sum(v.get("rpm_usd", 0) for v in lf_with_ctr) / max(len(lf_with_ctr), 1):.2f}</td></tr>
      </table></div>
    </div>
  </div>
  """}

  <!-- Year table -->
  <div class="card">
    <div class="card-title">📉 Year-by-Year: Shorts vs Long-form</div>
    <div class="table-wrap"><table>
      <tr><th>Year</th><th>Overall Avg</th><th>Videos</th><th style="color:var(--red)">Shorts Avg</th><th style="color:var(--green)">Long-form Avg</th></tr>
      {year_rows()}
    </table></div>
  </div>

  <!-- AI Chat -->
  <div class="card">
    <div class="card-title">🤖 Ask Claude About Your Analytics</div>
    <div class="chat-wrap">

      <!-- API key setup -->
      <div class="chat-key-bar" id="key-bar">
        <span>🔑</span>
        <input type="password" id="api-key-input" placeholder="Paste your Anthropic API key to enable chat (stored locally, never uploaded)" />
        <button onclick="saveKey()">Save</button>
        <button onclick="clearKey()" style="background:#f3f4f6;color:var(--text-muted)">Clear</button>
      </div>
      <div id="key-saved" style="display:none;align-items:center;gap:8px;padding:8px 12px;background:#f0fdf4;border-radius:8px;font-size:.83rem;color:var(--green)">
        ✅ API key saved locally &nbsp;<button onclick="clearKey()" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:.8rem;text-decoration:underline;padding:0">Remove</button>
      </div>

      <!-- Suggestion chips -->
      <div class="chat-suggestions">
        <span class="chat-chip" onclick="askChip(this)">Why did views drop in late 2025?</span>
        <span class="chat-chip" onclick="askChip(this)">What's our best month ever and why?</span>
        <span class="chat-chip" onclick="askChip(this)">Which day should we post for max views?</span>
        <span class="chat-chip" onclick="askChip(this)">What long-form length gets the most views?</span>
        <span class="chat-chip" onclick="askChip(this)">What's our fastest path to 100K avg views?</span>
      </div>

      <!-- Messages -->
      <div class="chat-messages" id="chat-messages">
        <div class="chat-bubble assistant">Hi! I'm Claude. I have full access to your OKStorytime channel data — {len(videos):,} videos, every monthly trend, day-of-week performance, and more. Ask me anything about why your views changed, what's working, or how to grow. Add your API key above to get started.</div>
      </div>

      <!-- Input -->
      <div class="chat-input-row">
        <textarea id="chat-input" rows="1" placeholder="Ask about your analytics… (Enter to send, Shift+Enter for new line)"
          oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'"
          onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();sendChat()}}"></textarea>
        <button class="chat-send" id="chat-send" onclick="sendChat()" title="Send">➤</button>
      </div>
    </div>
  </div>

</div>


<!-- ════════ TITLES ════════ -->
<div id="tab-titles" class="tab">
  <div class="card" style="margin-top:22px">
    <div class="card-title">🔤 Title Keywords: What Works vs What Doesn't</div>
    <div class="two-col">
      <div>
        <h3 style="color:var(--green);margin-bottom:12px;font-size:.85rem;font-weight:700">✅ Words in your TOP 25% of videos</h3>
        <div class="table-wrap"><table>
          <tr><th>Keyword</th><th>How much more common</th></tr>
          {kw_rows(top_kw, "+", "var(--green)")}
        </table></div>
        <p style="font-size:.82rem;color:var(--text-muted);margin-top:10px">Use: <span class="tag g">truth</span><span class="tag g">dark</span><span class="tag g">reaction</span><span class="tag g">hours</span><span class="tag g">proposed</span><span class="tag g">abandoned</span><span class="tag g">secret</span></p>
      </div>
      <div>
        <h3 style="color:var(--red);margin-bottom:12px;font-size:.85rem;font-weight:700">❌ Words in your BOTTOM 25% of videos</h3>
        <div class="table-wrap"><table>
          <tr><th>Keyword</th><th>How much more common</th></tr>
          {kw_rows(bot_kw, "−", "var(--red)")}
        </table></div>
        <p style="font-size:.82rem;color:var(--text-muted);margin-top:10px">Avoid: <span class="tag r">clip</span><span class="tag r">tifu</span><span class="tag r">flag</span><span class="tag r">denise</span><span class="tag r">brady</span><span class="tag r">joanna</span></p>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">💡 Title Formula That Works</div>
    <div class="insight green"><strong>Formula:</strong> [Shocking first-person action] + [unresolved twist hint]<br>Top 10 examples: "My husband BONED his co-worker…" · "I was accused of cheating… the DNA Test revealed the TRUTH" · "Stepdad wants me to pay rent… but he doesn't know my secret"</div>
    <div class="insight red"><strong>Avoid:</strong> Titles that lead with subreddit tag, host name, or generic clip label.<br>Not working: "r/tifu clip" · "Denise reacts to…" · "Full episode" · "Red flag / Green flag"</div>
    <div class="insight yellow">💡 Every viral title pattern: <strong>first-person drama + unresolved tension</strong>. The viewer must feel they NEED to know what happens next.</div>
  </div>

  <!-- Title Generator -->
  <!-- Title Performance -->
  <div class="card">
    <div class="card-title">📋 Title Performance — What's Working &amp; What's Not</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 12px">Click a time period to filter. Titles link directly to the video.</p>
    <div id="title-period-filters" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
      <button class="filter-btn active" onclick="filterTitles('all',this)">All Time</button>
      <button class="filter-btn" onclick="filterTitles('2026',this)">2026</button>
      <button class="filter-btn" onclick="filterTitles('2025',this)">2025</button>
      <button class="filter-btn" onclick="filterTitles('2024',this)">2024</button>
      <button class="filter-btn" onclick="filterTitles('2023',this)">2023</button>
      <button class="filter-btn" onclick="filterTitles('last30',this)">Last 30 Days</button>
    </div>
    <div class="two-col" style="gap:14px">
      <div>
        <div style="font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--green);margin-bottom:8px">✓ Top Performing Titles</div>
        <div id="title-top-list" style="display:flex;flex-direction:column;gap:6px"></div>
      </div>
      <div>
        <div style="font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--red);margin-bottom:8px">⚠ Lowest Performing Titles</div>
        <div id="title-bot-list" style="display:flex;flex-direction:column;gap:6px"></div>
      </div>
    </div>
  </div>

  <!-- Competitor Titles -->
  <div class="card">
    <div class="card-title">🏆 Competitor Titles</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 12px">What your competitors are titling. Study the patterns — sorted by views.</p>
    <div id="comp-title-filters" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
      <button class="filter-btn" onclick="filterCompTitles('all',this)">Lifetime</button>
      <button class="filter-btn" onclick="filterCompTitles('year',this)">This Year</button>
      <button class="filter-btn" onclick="filterCompTitles('30d',this)">Last 30 Days</button>
      <button class="filter-btn active" onclick="filterCompTitles('7d',this)">Last 7 Days</button>
    </div>
    <div id="comp-title-list" style="display:flex;flex-direction:column;gap:6px"></div>
    <button id="comp-title-more" onclick="showMoreCompTitles()" style="display:none;margin-top:12px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:8px 20px;font-size:.85rem;font-weight:600;cursor:pointer;color:var(--primary);width:100%">Show 25 More</button>
  </div>

  <!-- Title Pattern Hypothesis Analyzer -->
  <div class="card">
    <div class="card-title">🔬 Title Pattern Analysis — What Structure Wins?</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin:0 0 14px">Auto-analyzed from your video data. Sam's question: "Is it better to say <em>I got tricked</em> or <em>they tricked me</em>?" Here's what your data says.</p>
    <div id="title-patterns" class="two-col" style="gap:14px"></div>
  </div>

  <div class="card" id="title-gen-card">
    <div class="card-title">✍️ Title Generator — Paste Your Story, Get 5–10 Titles</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 14px">Claude will generate titles tuned to your channel's proven patterns — using the keyword data above and your top-performing title formulas. Requires your Anthropic API key (saved in the Analytics tab).</p>
    <div style="display:flex;flex-direction:column;gap:10px">
      <textarea id="story-input" rows="5" placeholder="Paste a short summary of the story or episode here… e.g. 'A wife discovers her husband has been secretly paying his ex's rent for two years while telling her they were broke. She finds out when the ex shows up at their door.'"
        style="width:100%;padding:12px 14px;border:1px solid var(--border);border-radius:10px;font-family:inherit;font-size:.875rem;line-height:1.55;resize:vertical;background:var(--surface2);color:var(--text);outline:none;box-sizing:border-box"></textarea>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button id="gen-btn" onclick="generateTitles()" style="background:var(--primary);color:#fff;border:none;border-radius:8px;padding:9px 20px;font-size:.875rem;font-weight:600;cursor:pointer">Generate Titles ✨</button>
        <span id="gen-status" style="font-size:.82rem;color:var(--text-muted)"></span>
      </div>
    </div>
    <div id="title-results" style="display:none;margin-top:18px;display:none">
      <div style="font-size:.75rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--text-muted);margin-bottom:10px">Generated Titles</div>
      <ol id="title-list" style="margin:0;padding-left:20px;display:flex;flex-direction:column;gap:8px;font-size:.9rem;line-height:1.5"></ol>
    </div>
  </div>
</div>


<!-- ════════ VIDEOS ════════ -->
<div id="tab-videos" class="tab">
  <!-- Sub-tab nav -->
  <div style="display:flex;gap:8px;margin:22px 0 16px;border-bottom:1px solid var(--border);padding-bottom:0">
    <button class="sub-tab-btn active" onclick="showVideoSub('longform',this)" style="padding:8px 18px;border:none;background:none;font-weight:700;font-size:.9rem;cursor:pointer;border-bottom:2px solid var(--primary);color:var(--primary);font-family:inherit">📹 Long-Form</button>
    <button class="sub-tab-btn" onclick="showVideoSub('shorts',this)" style="padding:8px 18px;border:none;background:none;font-weight:700;font-size:.9rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit">⚡ Shorts</button>
  </div>

  <!-- Long-form sub-tab -->
  <div id="video-sub-longform">
    <!-- Filters row -->
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap">
      <span style="font-size:.82rem;color:var(--text-muted);font-weight:600">Time:</span>
      <button class="vid-time-btn active" onclick="setVidTime('all',this)" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--primary);color:#fff;font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">All Time</button>
      <button class="vid-time-btn" onclick="setVidTime('year',this)" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Last Year</button>
      <button class="vid-time-btn" onclick="setVidTime('30d',this)" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Last 30 Days</button>
      <button class="vid-time-btn" onclick="setVidTime('7d',this)" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Last 7 Days</button>
      <span style="width:1px;height:20px;background:var(--border);margin:0 4px"></span>
      <span style="font-size:.82rem;color:var(--text-muted);font-weight:600">Livestreams:</span>
      <button class="live-filter-btn active" onclick="filterLiveRows(this,'hide')" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--primary);color:#fff;font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Hide</button>
      <button class="live-filter-btn" onclick="filterLiveRows(this,'all')" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Show All</button>
      <button class="live-filter-btn" onclick="filterLiveRows(this,'only')" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Livestreams Only</button>
    </div>
    <div class="card">
      <div class="card-title">🏆 Top 20 Long-Form Videos (5+ min)</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:10px">Sorted by views within the selected time period — click any title to watch.</p>
      <div class="table-wrap" id="lf-top-table"></div>
    </div>
    <div class="card">
      <div class="card-title">⚠️ Lowest Performing Long-Form</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:10px">Long-form videos with fewest views in the selected period. Study what went wrong.</p>
      <div class="table-wrap" id="lf-bot-table"></div>
    </div>

    <!-- Video Analysis Chat Bot -->
    <div class="card" style="margin-top:6px">
      <div class="card-title">🤖 Video Analysis Bot</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:12px">Ask questions about your videos: "Why did this title work?", "Compare my Sunday vs Wednesday uploads", "What do my top 10 have in common?"</p>
      <div class="chat-messages" id="vid-chat-messages" style="min-height:120px;max-height:360px;overflow-y:auto;display:flex;flex-direction:column;gap:12px;padding:4px 0 16px">
        <div class="chat-bubble assistant">I can analyze your {len(videos):,} videos. Ask me things like:<br>• "Why did my top videos work?"<br>• "Compare these two titles"<br>• "What day should I upload?"<br>• "What topics get the most views?"<br><br>Add your API key in the Analytics tab to get started.</div>
      </div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <input type="text" id="vid-chat-input" placeholder="Ask about your videos..." style="flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:10px;background:var(--surface);color:var(--text);font-size:.875rem;font-family:inherit" onkeydown="if(event.key==='Enter')askVideoBot()">
        <button onclick="askVideoBot()" style="padding:10px 20px;background:var(--primary);color:#fff;border:none;border-radius:10px;font-weight:700;cursor:pointer;font-family:inherit;font-size:.85rem">Ask</button>
      </div>
    </div>
  </div>

  <!-- Shorts sub-tab -->
  <div id="video-sub-shorts" style="display:none">
    <!-- Shorts time filter -->
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
      <span style="font-size:.82rem;color:var(--text-muted);font-weight:600">Time:</span>
      <button class="shorts-time-btn active" onclick="setShortsTime('all',this)" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--primary);color:#fff;font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">All Time</button>
      <button class="shorts-time-btn" onclick="setShortsTime('year',this)" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Last Year</button>
      <button class="shorts-time-btn" onclick="setShortsTime('30d',this)" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Last 30 Days</button>
      <button class="shorts-time-btn" onclick="setShortsTime('7d',this)" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Last 7 Days</button>
    </div>
    <div class="card">
      <div class="card-title">⚡ Top 20 Shorts</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:10px">Top Shorts by views in the selected time period.</p>
      <div class="table-wrap" id="shorts-top-table"></div>
    </div>
    <div class="card">
      <div class="card-title">⚠️ Lowest Performing Shorts</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:10px">Shorts with fewest views in the selected period.</p>
      <div class="table-wrap" id="shorts-bot-table"></div>
    </div>
  </div>
</div>


<!-- ════════ COMPETITORS ════════ -->
<div id="tab-competitors" class="tab">
  <div class="card" style="margin-top:22px">
    <div class="card-title">🏆 Competitor Landscape — Reddit Story Niche</div>
    <div class="table-wrap"><table class="comp-table">
      <tr><th>Channel</th><th>Subs</th><th>Avg Views</th><th>Format</th><th>Key Strength</th></tr>
      <tr><td>MrBallen</td><td>10.7M</td><td>1–3M</td><td>Solo narrator, 28 min</td><td>Easter eggs force full watch-throughs, $343K–$475K/mo ads</td></tr>
      <tr><td>rSlash</td><td>1.95M</td><td>~50K</td><td>Audio narration</td><td>2,000+ video library, strong Patreon</td></tr>
      <tr><td>Two Hot Takes</td><td>875K</td><td>~150K</td><td>Multi-host reaction</td><td>Strong TikTok funnel (812K), iHeart distributed</td></tr>
      <tr class="highlight-row"><td><strong>OKStorytime (you)</strong></td><td><strong>182K</strong></td><td><strong>~22K</strong></td><td><strong>4-host live show</strong></td><td><strong>Apple Podcasts #75 Comedy, iHeart distributed</strong></td></tr>
      <tr><td>Comfort Level</td><td>176K YT</td><td>Unknown</td><td>Multi-host podcast</td><td>TikTok-first (812K followers)</td></tr>
      <tr><td>PRIVATE DIARY</td><td>750K</td><td>~30K</td><td>TTS/animated narration</td><td>Consistent aesthetic, faceless format</td></tr>
      <tr><td>Las Damitas Histeria 🇲🇽</td><td>355K YT / 13.4M TikTok</td><td>70K–150K (episodes)</td><td>2-host comedy podcast + clip machine</td><td>Franchise model: book, live touring, Patreon + Sonoro network</td></tr>
    </table></div>
  </div>

  <div class="card">
    <div class="card-title">🔍 Competitor Deep Dives — Click to Expand</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin:0 0 14px">Each competitor broken down into what they do differently, what we can steal, and what we're already doing better.</p>

    <!-- MrBallen -->
    <details class="comp-dropdown" style="margin-bottom:10px">
      <summary class="comp-summary">
        <span style="font-weight:700;font-size:.95rem">MrBallen</span>
        <span style="font-size:.78rem;color:var(--text-muted);margin-left:8px">10.7M subs · Solo narrator · 1–3M avg views</span>
      </summary>
      <div class="comp-detail">
        <div class="comp-section-grid">
          <div>
            <div class="comp-section-label" style="color:var(--primary)">What They Do Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Easter egg system.</strong> He hides something in every video and pins the first comment that finds it — forces full watch-throughs and creates a game layer on top of content.</div>
            <div class="insight green" style="margin-bottom:8px"><strong>True crime adjacent positioning.</strong> He's not "Reddit stories" — he's "strange, dark, and mysterious." This earns $6–12 RPM vs the $4–8 RPM typical Reddit story channels get.</div>
            <div class="insight green"><strong>Cinematic production.</strong> B-roll, sound design, dramatic pacing. Every video feels like a mini-documentary, not a podcast recording.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--yellow)">What We Can Steal</div>
            <div class="insight yellow" style="margin-bottom:8px"><strong>The Easter egg concept.</strong> Hide a callback joke or "story of the week" answer that only makes sense if you watched the whole episode. Pin first comment that finds it.</div>
            <div class="insight yellow"><strong>The hook formula.</strong> MrBallen opens every video with a 15-second dramatic summary that makes the story sound impossible. Apply this to your cold opens.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--green)">What We're Doing Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Multi-host energy.</strong> MrBallen is solo — you have 4 hosts reacting in real-time. That creates natural comedy and unpredictable moments he can't replicate.</div>
            <div class="insight green"><strong>Live show format.</strong> Your content is recorded live with audience interaction. MrBallen is heavily scripted and edited. Your authenticity is your edge.</div>
          </div>
        </div>
      </div>
    </details>

    <!-- Two Hot Takes -->
    <details class="comp-dropdown" style="margin-bottom:10px">
      <summary class="comp-summary">
        <span style="font-weight:700;font-size:.95rem">Two Hot Takes</span>
        <span style="font-size:.78rem;color:var(--text-muted);margin-left:8px">875K subs · Multi-host reaction · ~150K avg views</span>
      </summary>
      <div class="comp-detail">
        <div class="comp-section-grid">
          <div>
            <div class="comp-section-label" style="color:var(--primary)">What They Do Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>TikTok funnel.</strong> They clip the most shocking 30-second moment from every episode for TikTok. Their TikTok (812K) feeds YouTube directly.</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Celebrity guest episodes.</strong> Regular guests from Bachelor Nation and podcasting bring built-in audiences to each episode.</div>
            <div class="insight green"><strong>iHeart distribution.</strong> Audio podcast distributed through iHeart gives them a second discovery engine beyond YouTube.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--yellow)">What We Can Steal</div>
            <div class="insight yellow" style="margin-bottom:8px"><strong>The TikTok clip funnel.</strong> You have 1.1M TikTok followers — more than them. You should be posting the single most shocking 30-second moment from every episode to TikTok with a "full video on YouTube" CTA.</div>
            <div class="insight yellow"><strong>Guest cross-promotion.</strong> When you have guests, make them share the episode to their audience. Their guests always do — yours should too.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--green)">What We're Doing Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Bigger TikTok presence.</strong> You have 1.1M TikTok followers vs their 812K. You're under-leveraging a larger platform.</div>
            <div class="insight green"><strong>Apple Podcasts #75 Comedy.</strong> Your podcast is ranking higher than theirs despite fewer YouTube subs. The audio audience is loyal.</div>
          </div>
        </div>
      </div>
    </details>

    <!-- rSlash -->
    <details class="comp-dropdown" style="margin-bottom:10px">
      <summary class="comp-summary">
        <span style="font-weight:700;font-size:.95rem">rSlash</span>
        <span style="font-size:.78rem;color:var(--text-muted);margin-left:8px">1.95M subs · Audio narration · ~50K avg views</span>
      </summary>
      <div class="comp-detail">
        <div class="comp-section-grid">
          <div>
            <div class="comp-section-label" style="color:var(--primary)">What They Do Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Insane consistency.</strong> 2,000+ video library. Same format every time. Viewers know exactly what they're getting. The algorithm loves predictability.</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Faceless/audio-only format.</strong> Zero production overhead. Can upload daily without burnout because there's no set, no camera, no editing beyond audio.</div>
            <div class="insight green"><strong>Strong Patreon.</strong> Monetizes superfans directly. Doesn't rely solely on ad revenue.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--yellow)">What We Can Steal</div>
            <div class="insight yellow" style="margin-bottom:8px"><strong>The consistent format structure.</strong> Every rSlash video follows the exact same template. Your show format is strong but thumbnail/title inconsistency confuses new visitors. Lock in a visual template.</div>
            <div class="insight yellow"><strong>Subreddit-specific episodes.</strong> rSlash's best performers are titled by subreddit (r/ProRevenge, r/AITA). Consider organizing your long-form by theme the same way.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--green)">What We're Doing Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Personality and faces.</strong> rSlash is faceless audio. You have real hosts with reactions, expressions, and chemistry. That creates a parasocial connection he can never build.</div>
            <div class="insight green"><strong>Higher ceiling per video.</strong> Your top videos hit 1M+. rSlash rarely breaks 200K. Your format has more viral potential when the title/thumbnail hit.</div>
          </div>
        </div>
      </div>
    </details>

    <!-- Comfort Level -->
    <details class="comp-dropdown" style="margin-bottom:10px">
      <summary class="comp-summary">
        <span style="font-weight:700;font-size:.95rem">Comfort Level</span>
        <span style="font-size:.78rem;color:var(--text-muted);margin-left:8px">176K subs · Multi-host podcast · TikTok-first</span>
      </summary>
      <div class="comp-detail">
        <div class="comp-section-grid">
          <div>
            <div class="comp-section-label" style="color:var(--primary)">What They Do Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>TikTok-first strategy.</strong> They built 812K TikTok followers before YouTube. Every YouTube video is promoted through TikTok clips first.</div>
            <div class="insight green"><strong>Younger, trend-aware positioning.</strong> They lean into current internet culture and trending topics more aggressively than traditional storytime channels.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--yellow)">What We Can Steal</div>
            <div class="insight yellow"><strong>TikTok-to-YouTube pipeline.</strong> They prove the funnel works at your size. Clip the best 30 seconds, post to TikTok, link full video. You already have the TikTok audience — just need the bridge content.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--green)">What We're Doing Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Larger YouTube subscriber base.</strong> You have more YouTube subs and a longer track record. Your library is a massive asset they don't have yet.</div>
            <div class="insight green"><strong>Podcast distribution.</strong> Apple Podcasts #75 Comedy. They don't have comparable audio distribution.</div>
          </div>
        </div>
      </div>
    </details>

    <!-- Charlotte Dobre -->
    <details class="comp-dropdown" style="margin-bottom:10px">
      <summary class="comp-summary">
        <span style="font-weight:700;font-size:.95rem">Charlotte Dobre</span>
        <span style="font-size:.78rem;color:var(--text-muted);margin-left:8px">Solo host · Reaction/commentary format</span>
      </summary>
      <div class="comp-detail">
        <div class="comp-section-grid">
          <div>
            <div class="comp-section-label" style="color:var(--primary)">What They Do Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Solo host brand.</strong> Charlotte IS the brand. Her face, her reactions, her personality — viewers subscribe for her specifically, not the stories.</div>
            <div class="insight green"><strong>Aggressive thumbnail formula.</strong> Every thumbnail: extreme close-up, mouth open, direct eye contact, bold text overlay. Zero variation = instant recognition in feed.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--yellow)">What We Can Steal</div>
            <div class="insight yellow"><strong>The no-variation thumbnail approach.</strong> Her thumbnails are basically a template. Same face position, same expression, same framing. You should lock Sam's face into the same template every video.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--green)">What We're Doing Differently</div>
            <div class="insight green"><strong>Multi-host dynamic.</strong> Charlotte is solo. Your group chemistry creates moments she can't — disagreements, reactions, humor between hosts. That's more entertaining and harder to replicate.</div>
          </div>
        </div>
      </div>
    </details>

    <!-- Am I the Jerk -->
    <details class="comp-dropdown" style="margin-bottom:10px">
      <summary class="comp-summary">
        <span style="font-weight:700;font-size:.95rem">Am I the Jerk?</span>
        <span style="font-size:.78rem;color:var(--text-muted);margin-left:8px">1.24M subs · Voice-acted narration</span>
      </summary>
      <div class="comp-detail">
        <div class="comp-section-grid">
          <div>
            <div class="comp-section-label" style="color:var(--primary)">What They Do Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Voice-acted characters.</strong> Multiple character voices make clips feel dramatic — like a radio play, not someone reading Reddit. This keeps retention high.</div>
            <div class="insight green"><strong>Massive Shorts revenue.</strong> Estimated $30K/mo from Shorts alone. They figured out the Short-form algorithm better than most in this niche.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--yellow)">What We Can Steal</div>
            <div class="insight yellow"><strong>Character voice moments.</strong> You don't need to voice-act every story, but having hosts briefly "become" a character when reading dialogue makes the content more dynamic.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--green)">What We're Doing Differently</div>
            <div class="insight green"><strong>Real human reactions.</strong> Their content is scripted and voice-acted. Yours is genuine reactions from real people. Authenticity creates deeper loyalty even if their production is slicker.</div>
          </div>
        </div>
      </div>
    </details>

    <!-- Las Damitas Histeria -->
    <details class="comp-dropdown" style="margin-bottom:10px">
      <summary class="comp-summary">
        <span style="font-weight:700;font-size:.95rem">Las Damitas Histeria 🇲🇽</span>
        <span style="font-size:.78rem;color:var(--text-muted);margin-left:8px">355K subs · 13.4M TikTok · 2-host comedy podcast</span>
      </summary>
      <div class="comp-detail">
        <p style="font-size:.82rem;color:var(--text-muted);margin:0 0 12px">2-host Mexican comedy podcast under Sonoro network. Founded Feb 2023. 50M+ views, live touring shows across Latin America and Europe. This is what a fully scaled version of OKStorytime looks like — in another language.</p>
        <div class="comp-section-grid">
          <div>
            <div class="comp-section-label" style="color:var(--primary)">What They Do Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Verdict format, not storytime.</strong> They don't just read stories — they render a verdict. Every episode has a clear "who's right / who's wrong" outcome the audience votes on. Viewers aren't passive — they're jury members.</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Proprietary vocabulary builds a tribe.</strong> "Damita" (their fan name), "Ramiro" (their word for any bad boyfriend), "histeriquilla/eneje" (verdict labels) — their audience adopts this language. Turns viewers into club members.</div>
            <div class="insight green" style="margin-bottom:8px"><strong>Clip machine strategy.</strong> One weekly 60-min recording session produces: 1 full episode + 4–6 standalone clips, all published across the same week.</div>
            <div class="insight green"><strong>Audio + video simultaneously.</strong> Episodes work perfectly as podcasts on Spotify AND as YouTube videos. Doubles their distribution.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--yellow)">What We Can Steal</div>
            <div class="insight yellow" style="margin-bottom:8px"><strong>Give your audience a verdict name.</strong> "OKStorytime Jury," "The Council," "The Verdict Squad" — something that makes fans feel like participants, not viewers.</div>
            <div class="insight yellow" style="margin-bottom:8px"><strong>Name the villain archetype.</strong> "Ramiro" is genius — create your own recurring nickname for the antagonist in stories. Inside-joke culture keeps people coming back.</div>
            <div class="insight yellow"><strong>The clip machine model.</strong> You're already recording 1–3 hour live shows. Extract 4–6 standalone clips per episode — most shocking moment, funniest reaction, best verdict, biggest disagreement.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--green)">What We're Doing Differently</div>
            <div class="insight green" style="margin-bottom:8px"><strong>English-language market.</strong> You're in the largest YouTube market. Their 355K subs in Spanish = your 182K subs in English is actually comparable reach given market size.</div>
            <div class="insight green"><strong>4 hosts vs 2.</strong> More hosts = more personality combinations, more reaction variety, more disagreement. That's harder to replicate.</div>
          </div>
        </div>
      </div>
    </details>

    <!-- Mark Narrations -->
    <details class="comp-dropdown" style="margin-bottom:10px">
      <summary class="comp-summary">
        <span style="font-weight:700;font-size:.95rem">Mark Narrations</span>
        <span style="font-size:.78rem;color:var(--text-muted);margin-left:8px">Solo narrator · Reddit story niche</span>
      </summary>
      <div class="comp-detail">
        <div class="comp-section-grid">
          <div>
            <div class="comp-section-label" style="color:var(--primary)">What They Do Differently</div>
            <div class="insight green"><strong>High-volume consistency.</strong> Daily uploads with extremely consistent formatting. The algorithm rewards the predictability.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--yellow)">What We Can Steal</div>
            <div class="insight yellow"><strong>Subreddit-tagged titles.</strong> Mark's titles always include the subreddit, which acts as a keyword and genre signal for both viewers and the algorithm.</div>
          </div>
          <div>
            <div class="comp-section-label" style="color:var(--green)">What We're Doing Differently</div>
            <div class="insight green"><strong>Production value and personality.</strong> Mark is solo narration. Your multi-host live format creates genuine entertainment value beyond just reading stories.</div>
          </div>
        </div>
      </div>
    </details>

  </div>

  <div class="card">
    <div class="card-title">💰 The CPM Opportunity</div>
    <div class="insight yellow"><strong>Relationship/AITA drama earns $4–8 RPM.</strong> True crime adjacent content earns $6–12 RPM. You're in the right niche — you just need the views to capitalize on it.</div>
  </div>
</div>

<!-- ════════ SHORTS STRATEGY ════════ -->
<div id="tab-shorts" class="tab">
  <div class="card" style="margin-top:22px">
    <div class="card-title">⚡ Shorts Strategy — The Full Picture</div>
    <p style="font-size:.84rem;color:var(--text-muted);margin:0 0 14px">Your Shorts peaked at 60,453 avg views in 2023. They now average 4,784 in 2025 — a 92% decline. Here's the data, what went wrong, and how to fix it.</p>
    <div class="table-wrap"><table>
      <tr><th>Year</th><th>Avg Shorts Views</th><th>Shorts Made</th><th>vs Long-form</th></tr>
      {shorts_year_rows()}
    </table></div>
  </div>

  <div class="two-col" style="gap:14px">
    <div class="card">
      <div class="card-title">🔍 Why Your Shorts Collapsed</div>
      <div class="insight red" style="margin-bottom:10px">❌ <strong>YouTube killed Short-form Shorts in 2023.</strong> The platform shifted its algorithm from rewarding viral Shorts to rewarding watch time. Short clips under 30s that used to hit 100K+ now barely register.</div>
      <div class="insight red" style="margin-bottom:10px">❌ <strong>Volume killed quality signal.</strong> 547 Shorts in 2025 = one every 16 hours. YouTube's algorithm can't figure out which ones to push — so it pushes none of them.</div>
      <div class="insight red" style="margin-bottom:10px">❌ <strong>Wrong format for the current algorithm.</strong> Your top Shorts were reaction clips cut from livestreams. Those feel like leftover content. The algorithm now rewards Shorts made intentionally for the format.</div>
      <div class="insight yellow">💡 <strong>The current winning Shorts format:</strong> 15–33 seconds, mid-action cold open (no intro), single shocking story moment, cliffhanger ending that drives to the long-form video.</div>
    </div>
    <div class="card">
      <div class="card-title">✅ How to Fix Shorts in 2026</div>
      <div class="insight green" style="margin-bottom:10px"><strong>1. Cut to 2–3 Shorts/week max.</strong> Pick your single best story moment from each long-form episode. One intentional Short beats 20 random clips every time.</div>
      <div class="insight green" style="margin-bottom:10px"><strong>2. Use the cliffhanger hook.</strong> Start mid-story: "She opened the door and couldn't believe what she saw…" — then cut. Link to the full episode in pinned comment.</div>
      <div class="insight green" style="margin-bottom:10px"><strong>3. Series format.</strong> "Part 1 / Part 2 / Part 3" Shorts force profile visits and subscriptions. Each Short ends on an unresolved beat.</div>
      <div class="insight green" style="margin-bottom:10px"><strong>4. Add trending audio in first 5 seconds.</strong> YouTube data shows this gives a 21% reach boost on Shorts.</div>
      <div class="insight green"><strong>5. Target length: 15–33 seconds.</strong> This range has the highest retention on Shorts. Under 15 feels too rushed. Over 45 loses viewers before the hook lands.</div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">🏆 Your Best Shorts Ever — Study These</div>
    <p style="font-size:.84rem;color:var(--text-muted);margin-bottom:4px">These worked. Look at the hook, the title format, the thumbnail. These are your templates.</p>
    {shorts_top_grid()}
  </div>

  <div class="card">
    <div class="card-title">⚠️ Worst Recent Shorts (2024+) — Stop Making These</div>
    <p style="font-size:.84rem;color:var(--text-muted);margin-bottom:4px">These are the patterns that are failing right now.</p>
    {shorts_bot_grid()}
  </div>

  <div class="card">
    <div class="card-title">🏆 What Competitors Are Doing With Shorts</div>
    <div class="table-wrap"><table class="comp-table">
      <tr><th>Channel</th><th>Subs</th><th>Shorts Strategy</th><th>What Works</th></tr>
      <tr><td>Two Hot Takes</td><td>875K</td><td>Best 30s moment per episode, TikTok cross-post</td><td>TikTok funnel (812K) feeds YouTube Shorts; consistent host faces</td></tr>
      <tr><td>rSlash</td><td>1.95M</td><td>Audio-only narration clips, subreddit-tagged</td><td>Massive library = algorithm keeps pushing old Shorts; no face needed</td></tr>
      <tr><td>Am I The Jerk?</td><td>1.24M</td><td>Voice-acted AITA clips, 30–45s each</td><td>Multiple character voices make clips feel dramatic; $30K/mo estimated</td></tr>
      <tr><td>Private Diary</td><td>750K</td><td>TTS narration over subtle animation</td><td>Faceless format scales infinitely; consistent aesthetic = instant recognition</td></tr>
      <tr><td>Karma Stories</td><td>138K</td><td>Daily uploads, justice/revenge focus</td><td>Enthusiastic narration + satisfying endings; r/ProRevenge performs best</td></tr>
      <tr class="highlight-row"><td><strong>OKStorytime (you)</strong></td><td><strong>1.5M</strong></td><td><strong>Livestream clips (not working)</strong></td><td><strong>Opportunity: intentional Shorts with your hosts' faces + cliffhanger format</strong></td></tr>
    </table></div>
    <div class="insight yellow" style="margin-top:14px">💡 <strong>Your unfair advantage:</strong> Every competitor above is either faceless or audio-only. You have actual hosts with personality and reactions. A tight 20-second clip of Sam's face reacting to a shocking story moment — with the cliffhanger cut — is something none of them can replicate.</div>
  </div>

  <div class="card">
    <div class="card-title">📋 Shorts Content Calendar Template</div>
    <div class="table-wrap"><table>
      <tr><th>Day</th><th>Action</th><th>Format</th><th>Source</th></tr>
      <tr><td><strong>Sunday</strong></td><td>Post flagship long-form episode</td><td>60–90 min</td><td>New episode</td></tr>
      <tr class="highlight-row"><td><strong>Monday</strong></td><td>Post Short #1 — best story hook from Sunday's episode</td><td>15–25 sec</td><td>Clip from Sunday</td></tr>
      <tr><td><strong>Wednesday</strong></td><td>Post 2nd long-form</td><td>40–60 min</td><td>New episode</td></tr>
      <tr class="highlight-row"><td><strong>Thursday</strong></td><td>Post Short #2 — cliffhanger from Wednesday</td><td>20–33 sec</td><td>Clip from Wednesday</td></tr>
      <tr><td><strong>Friday</strong></td><td>Optional: Short #3 — "reaction moment" or audience question</td><td>15–30 sec</td><td>Studio moment</td></tr>
    </table></div>
    <p style="font-size:.82rem;color:var(--text-muted);margin-top:10px">2–3 intentional Shorts/week tied to your long-form > 20+ random clips. Every Short should have a pinned comment linking to the full episode.</p>
  </div>
</div>

<!-- ════════ LAUNCH TRACKER ════════ -->
<div id="tab-tracker" class="tab">
  <div class="card" style="margin-top:22px">
    <div class="card-title">🚀 Weekly Launch Tracker — 10K in 48 Hours</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 16px">Track every video's progress toward the 10K/48hr goal. Action items update based on where each video is in its launch window.</p>

    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px">
      <div style="background:var(--surface);border:2px solid var(--border);border-radius:12px;padding:16px;text-align:center">
        <div style="font-size:2rem;font-weight:800;color:var(--primary)">{goal_7d["total"]}</div>
        <div style="font-size:.78rem;color:var(--text-muted);font-weight:600">Videos This Week</div>
      </div>
      <div style="background:var(--surface);border:2px solid var(--green);border-radius:12px;padding:16px;text-align:center">
        <div style="font-size:2rem;font-weight:800;color:var(--green)">{goal_7d["hit"]}</div>
        <div style="font-size:.78rem;color:var(--text-muted);font-weight:600">Hit 10K Goal</div>
      </div>
      <div style="background:var(--surface);border:2px solid var(--yellow);border-radius:12px;padding:16px;text-align:center">
        <div style="font-size:2rem;font-weight:800;color:var(--yellow)">{goal_7d["in_window"]}</div>
        <div style="font-size:.78rem;color:var(--text-muted);font-weight:600">Still in 48hr Window</div>
      </div>
      <div style="background:var(--surface);border:2px solid var(--red);border-radius:12px;padding:16px;text-align:center">
        <div style="font-size:2rem;font-weight:800;color:var(--red)">{goal_7d["missed"]}</div>
        <div style="font-size:.78rem;color:var(--text-muted);font-weight:600">Missed Goal</div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:18px">
      <div style="background:linear-gradient(135deg,var(--surface) 0%,var(--surface2) 100%);border:1px solid var(--border);border-radius:12px;padding:16px">
        <div style="font-size:.75rem;font-weight:700;color:var(--primary);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">7-Day Average</div>
        <div style="font-size:1.4rem;font-weight:800">{goal_7d["avg_views"]:,.0f} views</div>
        <div style="font-size:.78rem;color:var(--text-muted)">{goal_7d["hit"]}/{goal_7d["total"]} videos hit 10K ({(goal_7d["hit"]/max(goal_7d["total"],1)*100):.0f}%)</div>
      </div>
      <div style="background:linear-gradient(135deg,var(--surface) 0%,var(--surface2) 100%);border:1px solid var(--border);border-radius:12px;padding:16px">
        <div style="font-size:.75rem;font-weight:700;color:var(--primary);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">30-Day Average</div>
        <div style="font-size:1.4rem;font-weight:800">{goal_30d["avg_views"]:,.0f} views</div>
        <div style="font-size:.78rem;color:var(--text-muted)">{goal_30d["hit"]}/{goal_30d["total"]} videos hit 10K ({(goal_30d["hit"]/max(goal_30d["total"],1)*100):.0f}%)</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div style="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:14px">
      <button class="lt-tab-btn active" onclick="showLTTab('7d',this)" style="padding:7px 16px;border:none;background:none;font-weight:700;font-size:.85rem;cursor:pointer;border-bottom:2px solid var(--primary);color:var(--primary);font-family:inherit;margin-bottom:-2px">Last 7 Days</button>
      <button class="lt-tab-btn" onclick="showLTTab('14d',this)" style="padding:7px 16px;border:none;background:none;font-weight:600;font-size:.85rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit;margin-bottom:-2px">Last 14 Days</button>
      <button class="lt-tab-btn" onclick="showLTTab('30d',this)" style="padding:7px 16px;border:none;background:none;font-weight:600;font-size:.85rem;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-muted);font-family:inherit;margin-bottom:-2px">Last 30 Days</button>
    </div>
    <div id="lt-7d" class="lt-panel">{tracker_7d_html}</div>
    <div id="lt-14d" class="lt-panel" style="display:none">{tracker_14d_html}</div>
    <div id="lt-30d" class="lt-panel" style="display:none">{tracker_30d_html}</div>
  </div>
</div>

<!-- ════════ EXPERIMENTS ════════ -->
<div id="tab-experiments" class="tab">

  <!-- 10K/48hr Scorecard -->
  <div class="card" style="margin-top:22px">
    <div class="card-title">🎯 10K in 48 Hours — Pre-Launch Scorecard</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin:0 0 14px">Score your next upload BEFORE it goes live. Based on patterns from your top-performing videos. Sam's north star: 12%+ CTR in the first hour = we're back.</p>
    <div style="display:grid;grid-template-columns:1fr auto;gap:20px;align-items:start">
      <div class="exp-form">
        <input type="text" id="sc-title" placeholder="Working title for the video">
        <input type="text" id="sc-story" placeholder="One-line story hook (e.g. 'Wife finds husband's Tinder while pregnant')">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <select id="sc-host"><option value="sam">Sam (solo)</option><option value="john">John (solo)</option><option value="both">Sam + John</option><option value="guest">With Guest</option></select>
          <select id="sc-length"><option value="60-90">60-90 min (best)</option><option value="40-60">40-60 min</option><option value="20-40">20-40 min</option><option value="10-20">10-20 min</option><option value="90+">90+ min</option></select>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <select id="sc-day"><option value="Sunday">Sunday (best)</option><option value="Monday">Monday</option><option value="Tuesday">Tuesday</option><option value="Wednesday">Wednesday</option><option value="Thursday">Thursday</option><option value="Friday">Friday</option><option value="Saturday">Saturday</option></select>
          <select id="sc-thumb"><option value="closeup">Close-up face (recommended)</option><option value="studio">Studio shot</option><option value="guest">Guest thumbnail</option><option value="text">Text-heavy</option><option value="karen">Karen/character style</option></select>
        </div>
        <div style="display:flex;gap:8px">
          <label style="display:flex;align-items:center;gap:4px;font-size:.82rem"><input type="checkbox" id="sc-first-person" checked> First-person title ("I/My")</label>
          <label style="display:flex;align-items:center;gap:4px;font-size:.82rem"><input type="checkbox" id="sc-twist"> Has unresolved twist</label>
          <label style="display:flex;align-items:center;gap:4px;font-size:.82rem"><input type="checkbox" id="sc-relationship" checked> Relationship story</label>
        </div>
        <button onclick="scoreVideo()" style="background:var(--primary);color:#fff;border:none;border-radius:8px;padding:9px 20px;font-size:.875rem;font-weight:600;cursor:pointer;justify-self:start">Score This Video 🎯</button>
      </div>
      <div id="score-display" style="text-align:center;min-width:100px">
        <div class="score-ring score-mid" style="font-size:1.4rem;width:70px;height:70px">?</div>
        <div style="font-size:.75rem;color:var(--text-muted);margin-top:6px">Score / 100</div>
      </div>
    </div>
    <div id="score-breakdown" style="display:none;margin-top:16px;padding:14px;background:var(--surface2);border-radius:10px;font-size:.85rem;line-height:1.65"></div>
  </div>

  <!-- A/B Test Experiment Tracker -->
  <div class="card">
    <div class="card-title">🧪 A/B Test Experiment Tracker</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin:0 0 14px">Log your thumbnail and title experiments here. Track what you changed, your hypothesis, and the result. Data saved in your browser.</p>

    <div style="border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px;background:var(--surface2)">
      <div style="font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--primary);margin-bottom:10px">New Experiment</div>
      <div class="exp-form">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <input type="text" id="exp-video-url" placeholder="YouTube video URL or title">
          <select id="exp-type"><option value="thumbnail">Thumbnail A/B Test</option><option value="title">Title A/B Test</option><option value="both">Thumbnail + Title</option></select>
        </div>
        <textarea id="exp-hypothesis" placeholder="Your hypothesis... e.g. 'I think first-person titles (I got tricked) will get higher CTR than third-person (She tricked him)'"></textarea>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <input type="text" id="exp-variant-a" placeholder="Variant A (current/control)">
          <input type="text" id="exp-variant-b" placeholder="Variant B (new test)">
        </div>
        <input type="date" id="exp-start-date">
        <button onclick="addExperiment()" style="background:var(--primary);color:#fff;border:none;border-radius:8px;padding:9px 20px;font-size:.875rem;font-weight:600;cursor:pointer;justify-self:start">Log Experiment 🧪</button>
      </div>
    </div>

    <div style="font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:10px">Active Experiments</div>
    <div id="exp-list" class="exp-list">
      <p style="color:var(--text-muted);font-size:.85rem">No experiments logged yet. Start by adding one above.</p>
    </div>

    <div style="font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin:20px 0 10px">Completed Experiments</div>
    <div id="exp-completed" class="exp-list">
      <p style="color:var(--text-muted);font-size:.85rem">Complete an experiment to see results here.</p>
    </div>
  </div>

  <!-- Rules You've Discovered -->
  <div class="card">
    <div class="card-title">📏 Your Rules — What You've Proven Works</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin:0 0 14px">As you run experiments, add rules here. Sam said: "You should be creating rules constantly for what works, title and thumbnail wise, based on past data."</p>
    <div id="rules-list" style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px">
      <p style="color:var(--text-muted);font-size:.85rem">No rules yet. Run experiments and add what you learn.</p>
    </div>
    <div style="display:flex;gap:8px">
      <input type="text" id="new-rule" placeholder="e.g. First-person titles get 3x more CTR than third-person" style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:8px;font-family:inherit;font-size:.85rem">
      <button onclick="addRule()" style="background:var(--primary);color:#fff;border:none;border-radius:8px;padding:8px 16px;font-size:.85rem;font-weight:600;cursor:pointer">Add Rule</button>
    </div>
  </div>

</div>

<footer>Generated with Claude Code &nbsp;·&nbsp; OKStorytime YouTube Growth Report &nbsp;·&nbsp; {now}</footer>

<script>
// ── Tab navigation ──────────────────────────────────────────────
function show(tab, btn) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  btn.classList.add('active');
  window.scrollTo({{top: 80, behavior: 'smooth'}});
}}

// ── Embedded data ───────────────────────────────────────────────
const ALL_VIDEOS   = {video_json};
const MONTHLY_DATA = {monthly_json};
const THUMB_DICT   = {thumb_dict_json};
const COMP_VIDEOS  = {comp_json};
const COMP_ALL     = {comp_all_json};
const COMP_THUMBS  = {comp_thumb_dict_json};

const DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
const BUCKETS = [
  ["5-10 min", 5, 10], ["10-20 min", 10, 20],
  ["20-40 min", 20, 40], ["40-60 min", 40, 60], ["60-90 min", 60, 90],
  ["90-120 min", 90, 120], ["120+ min", 120, 9999]
];

function fmtK(n) {{
  if (n >= 1000000) return (n/1000000).toFixed(1).replace(/[.]0$/, '') + 'M';
  if (n >= 1000)    return (n/1000).toFixed(1).replace(/[.]0$/, '') + 'K';
  return Math.round(n).toLocaleString();
}}

// ── Analytics filter (day/length tables) ───────────────────────
function filterAnalytics(year, btn) {{
  document.querySelectorAll('.filter-btn').forEach(b => {{
    if (b.closest('#chart-filters')) return;
    b.classList.remove('active');
  }});
  btn.classList.add('active');

  const allLF = ALL_VIDEOS.filter(v => v.dur >= 5);
  const vids = year === 'all' ? allLF : allLF.filter(v => v.y === parseInt(year));

  // Day of week table
  const dayTable = document.getElementById('day-table');
  let dayRows = '<tr><th>Day</th><th>Avg Views</th><th>Videos</th><th></th></tr>';
  const dayAvgs = {{}};
  DAYS.forEach(day => {{
    const dv = vids.filter(v => v.d === day);
    if (dv.length) dayAvgs[day] = {{ avg: dv.reduce((s,v) => s+v.v, 0)/dv.length, count: dv.length }};
  }});
  const maxDay   = Math.max(...Object.values(dayAvgs).map(d => d.avg));
  const bestDay  = Object.entries(dayAvgs).sort((a,b) => b[1].avg - a[1].avg)[0]?.[0];
  const worstDay = Object.entries(dayAvgs).sort((a,b) => a[1].avg - b[1].avg)[0]?.[0];
  DAYS.forEach(day => {{
    if (!dayAvgs[day]) return;
    const d = dayAvgs[day];
    const w = Math.max(4, Math.round(d.avg/maxDay*160));
    const hl = day === bestDay ? ' class="highlight-row"' : '';
    const star = day === bestDay ? '⭐ ' : '';
    const barCls = day === bestDay ? 'bar gold' : 'bar';
    dayRows += `<tr${{hl}}><td>${{star}}${{day}}</td><td class="num"><strong>${{fmtK(d.avg)}}</strong></td><td class="muted">${{d.count}}</td><td class="bar-cell"><div class="bar-wrap"><div class="${{barCls}}" style="width:${{w}}px"></div></div></td></tr>`;
  }});
  dayTable.innerHTML = dayRows;
  if (bestDay && worstDay) {{
    const ratio = (dayAvgs[bestDay].avg / dayAvgs[worstDay].avg).toFixed(1);
    document.getElementById('day-note').textContent = `${{bestDay}} is ${{ratio}}x better than ${{worstDay}}.`;
  }}

  // Length table
  const lenTable = document.getElementById('len-table');
  let lenRows = '<tr><th>Length</th><th>Avg Views</th><th>Videos</th><th></th></tr>';
  const lenData = [];
  BUCKETS.forEach(([label, lo, hi]) => {{
    const bv = vids.filter(v => v.dur >= lo && v.dur < hi);
    if (bv.length) lenData.push({{ label, avg: bv.reduce((s,v) => s+v.v,0)/bv.length, count: bv.length }});
  }});
  const maxLen  = Math.max(...lenData.map(d => d.avg));
  const bestLen = [...lenData].sort((a,b) => b.avg - a.avg)[0];
  lenData.forEach(d => {{
    const w = Math.max(4, Math.round(d.avg/maxLen*160));
    const hl = bestLen && d.label === bestLen.label ? ' class="highlight-row"' : '';
    const star = bestLen && d.label === bestLen.label ? '⭐ ' : '';
    const barCls = bestLen && d.label === bestLen.label ? 'bar gold' : 'bar';
    lenRows += `<tr${{hl}}><td>${{star}}${{d.label}}</td><td class="num"><strong>${{fmtK(d.avg)}}</strong></td><td class="muted">${{d.count}}</td><td class="bar-cell"><div class="bar-wrap"><div class="${{barCls}}" style="width:${{w}}px"></div></div></td></tr>`;
  }});
  lenTable.innerHTML = lenRows;
  if (bestLen) document.getElementById('len-note').textContent = `${{bestLen.label}} is your top format for this period.`;
}}

// ── Monthly Chart — YouTube Studio Style (Chart.js) ──────────────
let monthlyChart = null;
let _currentMetric = 'views';
let _currentRange = 12;

const METRIC_CONFIG = {{
  views:        {{ key: 'views',        label: 'Total Views',        color: '#7c3aed', fmt: v => fmtK(v),   tooltipLabel: 'views',          unit: '' }},
  watch_hours:  {{ key: 'watch_hours',  label: 'Watch Time (hrs)',   color: '#2563eb', fmt: v => fmtK(v),   tooltipLabel: 'hours watched',  unit: ' hrs' }},
  avg_duration: {{ key: 'avg_duration', label: 'Avg Duration (min)', color: '#059669', fmt: v => v.toFixed(1) + 'm', tooltipLabel: 'min avg', unit: ' min' }},
  ctr:          {{ key: 'ctr',          label: 'CTR (%)',            color: '#d97706', fmt: v => v.toFixed(2) + '%',  tooltipLabel: '% CTR',  unit: '%' }},
  revenue:      {{ key: 'revenue',      label: 'Revenue',            color: '#16a34a', fmt: v => '$' + fmtK(v),       tooltipLabel: 'revenue', unit: '' }},
}};

function setMetric(metric, btn) {{
  document.querySelectorAll('#metric-tabs .metric-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _currentMetric = metric;
  redrawChart();
}}
window.setMetric = setMetric;

function setChartRange(months, btn) {{
  document.querySelectorAll('#chart-filters .filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _currentRange = months;
  redrawChart();
}}

function redrawChart() {{
  const data = _currentRange === 0 ? MONTHLY_DATA : MONTHLY_DATA.slice(-_currentRange);
  drawChart(data, _currentMetric);
  updateMetricTabs(data);
}}

function updateMetricTabs(data) {{
  // Update each tab's summary value for the current time range
  Object.entries(METRIC_CONFIG).forEach(([key, cfg]) => {{
    const el = document.getElementById('mt-' + key + '-val');
    if (!el) return;
    const values = data.map(d => d[key] || 0);
    const total = values.reduce((a, b) => a + b, 0);
    if (key === 'views' || key === 'watch_hours' || key === 'revenue') {{
      el.textContent = cfg.fmt(total);
    }} else {{
      // For averages (CTR, avg_duration), show period average
      const nonZero = values.filter(v => v > 0);
      const avg = nonZero.length ? nonZero.reduce((a,b) => a+b, 0) / nonZero.length : 0;
      el.textContent = cfg.fmt(avg);
    }}
  }});
}}

function drawChart(data, metric) {{
  const cfg = METRIC_CONFIG[metric] || METRIC_CONFIG.views;
  const labels = data.map(d => d.month);
  const values = data.map(d => d[cfg.key] || 0);
  const counts = data.map(d => d.count);
  const peak   = Math.max(...values);
  const peakIdx = values.indexOf(peak);

  if (monthlyChart) monthlyChart.destroy();

  const ctx = document.getElementById('monthly-chart').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 320);
  grad.addColorStop(0, cfg.color + '40');
  grad.addColorStop(1, cfg.color + '00');

  monthlyChart = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels,
      datasets: [{{
        label: cfg.label,
        data: values,
        borderColor: cfg.color,
        backgroundColor: grad,
        borderWidth: 2.5,
        fill: true,
        tension: 0.35,
        pointRadius: data.length > 24 ? 2 : 4,
        pointHoverRadius: 7,
        pointBackgroundColor: values.map((v,i) => i === peakIdx ? '#f59e0b' : cfg.color),
        pointBorderColor: 'white',
        pointBorderWidth: 2,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ intersect: false, mode: 'index' }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#1e1148',
          titleColor: '#a78bfa',
          bodyColor: '#e2e8f0',
          padding: 14,
          cornerRadius: 10,
          callbacks: {{
            title: ctx => ctx[0].label,
            label: ctx => ' ' + cfg.fmt(ctx.parsed.y) + ' ' + cfg.tooltipLabel,
            afterLabel: ctx => ' ' + counts[ctx.dataIndex] + ' videos',
          }}
        }}
      }},
      scales: {{
        x: {{
          grid: {{ display: false }},
          border: {{ display: false }},
          ticks: {{ color: '#9ca3af', font: {{ size: 11 }}, maxTicksLimit: 14, maxRotation: 45 }}
        }},
        y: {{
          grid: {{ color: 'rgba(0,0,0,0.04)', drawBorder: false }},
          border: {{ display: false }},
          beginAtZero: metric === 'ctr' || metric === 'avg_duration',
          ticks: {{
            color: '#9ca3af',
            font: {{ size: 11 }},
            callback: v => cfg.fmt(v),
          }}
        }}
      }}
    }}
  }});

  // Update meta stats below chart
  const nonZero = values.filter(v => v > 0);
  const avg = nonZero.length ? nonZero.reduce((a,b) => a+b, 0) / nonZero.length : 0;
  const recent3 = values.slice(-3);
  const avg3 = recent3.length ? recent3.reduce((a,b) => a+b, 0) / recent3.length : 0;
  const isSumMetric = ['views','watch_hours','revenue'].includes(metric);
  const totalOrAvg = isSumMetric ? values.reduce((a,b)=>a+b,0) : avg;
  const totalLabel = isSumMetric ? 'Total in period' : 'Average over period';

  document.getElementById('chart-meta').innerHTML = `
    <div class="chart-stat"><span class="cs-val">${{cfg.fmt(peak)}}</span><br><span class="cs-lbl">Peak (${{labels[peakIdx]}})</span></div>
    <div class="chart-stat"><span class="cs-val">${{cfg.fmt(totalOrAvg)}}</span><br><span class="cs-lbl">${{totalLabel}}</span></div>
    <div class="chart-stat"><span class="cs-val" style="color:${{avg3 > avg ? 'var(--green)' : 'var(--red)'}}">${{cfg.fmt(avg3)}}</span><br><span class="cs-lbl">Last 3 months ${{avg3 > avg ? '▲ up' : '▼ down'}}</span></div>
    <div class="chart-stat"><span class="cs-val">${{data.reduce((s,d)=>s+d.count,0)}}</span><br><span class="cs-lbl">Videos in period</span></div>
  `;
}}

// ── Thumbnail filter ─────────────────────────────────────────────
let _thumbPeriod = 'all';
let _thumbSort = 'views';

function setThumbSort(sort, btn) {{
  document.querySelectorAll('#thumb-sort .filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  _thumbSort = sort;
  filterThumbs(_thumbPeriod, null);
}}

function filterThumbs(period, btn) {{
  if (btn) {{
    document.querySelectorAll('#thumb-filters .filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }}
  _thumbPeriod = period;

  const now = new Date();
  const longform = ALL_VIDEOS.filter(v => v.duration_minutes >= 5);

  const sortFn = _thumbSort === 'ctr' ? ((a,b) => (b.ctr||0) - (a.ctr||0))
               : _thumbSort === 'revenue' ? ((a,b) => (b.revenue||0) - (a.revenue||0))
               : ((a,b) => b.view_count - a.view_count);

  let pool;
  if (period === 'all') {{
    pool = [...longform].sort(sortFn);
  }} else if (period === 'last7') {{
    const cut = new Date(now - 7*86400000).toISOString().slice(0,10);
    pool = longform.filter(v => v.publish_date >= cut).sort(sortFn);
  }} else if (period === 'last30') {{
    const cut = new Date(now - 30*86400000).toISOString().slice(0,10);
    pool = longform.filter(v => v.publish_date >= cut).sort(sortFn);
  }} else {{
    const yr = parseInt(period);
    pool = longform.filter(v => v.publish_year === yr).sort(sortFn);
  }}

  const top18 = pool.filter(v => THUMB_DICT[v.video_id]).slice(0, 18);
  const grid = document.getElementById('top-thumb-grid');
  if (!grid) return;

  if (top18.length === 0) {{
    grid.innerHTML = '<p style="color:var(--text-muted);padding:12px 0">No thumbnails available for this period yet. They load on next refresh.</p>';
    return;
  }}

  grid.innerHTML = '<div class="thumb-grid">' + top18.map(v => {{
    const views = v.view_count >= 1000000 ? (v.view_count/1000000).toFixed(1)+'M'
                : v.view_count >= 1000    ? Math.round(v.view_count/1000)+'K'
                : v.view_count;
    const title = v.title.length > 45 ? v.title.slice(0,45)+'…' : v.title;
    // First-24h CTR badge (priority) or lifetime CTR
    const ctr24 = v.ctr_24h || 0;
    const ctr = v.ctr || 0;
    let ctrBadge = '';
    if (ctr24 > 0) {{
      const cls24 = ctr24 >= 12 ? 'ctr-great' : ctr24 >= 8 ? 'ctr-good' : ctr24 >= 4 ? 'ctr-ok' : 'ctr-low';
      ctrBadge = '<span class="ctr-badge '+cls24+'" title="First 24-hour CTR">'+ctr24.toFixed(1)+'% 24h</span>';
    }} else if (ctr > 0) {{
      const ctrCls = ctr >= 12 ? 'ctr-great' : ctr >= 8 ? 'ctr-good' : ctr >= 4 ? 'ctr-ok' : 'ctr-low';
      ctrBadge = '<span class="ctr-badge '+ctrCls+'" title="Lifetime CTR (not first 24h)">'+ctr.toFixed(1)+'% LT</span>';
    }}
    // Revenue badge
    const rev = v.revenue || 0;
    const revBadge = rev > 0 ? '<span class="rev-badge">$'+Math.round(rev).toLocaleString()+'</span>' : '';
    return '<div class="thumb-item">'
      + '<a href="https://youtube.com/watch?v='+v.video_id+'" target="_blank">'
      + '<img src="data:image/jpeg;base64,'+THUMB_DICT[v.video_id]+'" alt="'+title+'" loading="lazy"></a>'
      + '<div class="thumb-label">'
      + '<strong>'+views+' views</strong>'
      + '<div style="display:flex;gap:6px;margin:3px 0 4px;align-items:center;flex-wrap:wrap">'
      + '<span class="thumb-dur">'+Math.round(v.duration_minutes)+' min</span>'
      + ctrBadge + revBadge
      + '<span style="font-size:.68rem;color:var(--text-muted)">'+v.publish_date+'</span>'
      + '</div><span>'+title+'</span></div></div>';
  }}).join('') + '</div>';
}}

// ── Helpers ──────────────────────────────────────────────────────
function _esc(s) {{ return String(s).replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
function _isLive(title) {{ return /🔴|\\bstream\\b|\\blive\\b|\\bvod\\b/i.test(title); }}

// ── Title filter ─────────────────────────────────────────────────
function filterTitles(period, btn) {{
  document.querySelectorAll('#title-period-filters .filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const now = new Date();
  // Exclude livestreams (🔴) and shorts
  let pool = ALL_VIDEOS.filter(v => v.duration_minutes >= 5 && !_isLive(v.title));

  if (period === 'last30') {{
    const cut = new Date(now - 30*86400000).toISOString().slice(0,10);
    pool = pool.filter(v => v.publish_date >= cut);
  }} else if (period !== 'all') {{
    pool = pool.filter(v => v.publish_year === parseInt(period));
  }}

  const sorted = [...pool].sort((a,b) => b.view_count - a.view_count);
  const top15  = sorted.slice(0, 15);
  const bot15  = [...pool].sort((a,b) => a.view_count - b.view_count).slice(0, 15);

  function fmtV(n) {{
    return n >= 1000000 ? (n/1000000).toFixed(1)+'M' : n >= 1000 ? Math.round(n/1000)+'K' : n.toString();
  }}
  function titleRow(v, color) {{
    return '<a href="https://youtube.com/watch?v='+v.video_id+'" target="_blank" style="display:flex;align-items:baseline;gap:8px;padding:7px 10px;border-radius:8px;background:var(--surface2);border:1px solid var(--border);text-decoration:none;color:var(--text)">'
      + '<span style="font-size:.8rem;font-weight:700;color:'+color+';flex-shrink:0">'+fmtV(v.view_count)+'</span>'
      + '<span style="font-size:.85rem;flex:1;line-height:1.4">'+_esc(v.title)+'</span>'
      + '<span style="font-size:.7rem;color:var(--text-muted);flex-shrink:0">'+v.publish_date+'</span>'
      + '</a>';
  }}

  const topEl = document.getElementById('title-top-list');
  const botEl = document.getElementById('title-bot-list');
  if (topEl) topEl.innerHTML = top15.length ? top15.map(v => titleRow(v,'var(--green)')).join('') : '<p style="color:var(--text-muted);font-size:.85rem">No videos found for this period.</p>';
  if (botEl) botEl.innerHTML = bot15.length ? bot15.map(v => titleRow(v,'var(--red)')).join('')   : '<p style="color:var(--text-muted);font-size:.85rem">No videos found for this period.</p>';
}}

// ── Video tables (dynamic) ───────────────────────────────────────
let _vidTimePeriod = 'all';
let _vidLiveMode = 'hide';
let _shortsTimePeriod = 'all';

function _isLive(v) {{
  const t = (v.title || '').toLowerCase();
  return t.includes('\\uD83D\\uDD34') || t.includes('stream') || t.slice(0,20).includes('live');
}}

function _dateCutoff(period) {{
  const now = Date.now();
  if (period === '7d')   return new Date(now - 7*86400000).toISOString().slice(0,10);
  if (period === '30d')  return new Date(now - 30*86400000).toISOString().slice(0,10);
  if (period === 'year') return new Date(now - 365*86400000).toISOString().slice(0,10);
  return '1900-01-01';
}}

function _renderVideoTable(videos, color) {{
  if (!videos.length) return '<p style="color:var(--text-muted);font-size:.85rem">No videos found for this period.</p>';
  let html = '<table><tr><th>#</th><th>Title</th><th>Views</th><th>Length</th><th>Date</th><th>Day</th></tr>';
  videos.forEach((v, i) => {{
    const dur = v.dur || v.duration_minutes || 0;
    const durBadge = dur < 2
      ? '<span class="badge short">Short</span>'
      : '<span class="badge long">' + Math.round(dur) + 'm</span>';
    const numCls = color ? 'num ' + color : 'num';
    const title = (v.title || '').slice(0, 80);
    const url = 'https://youtube.com/watch?v=' + v.video_id;
    const day = (v.d || '').slice(0, 3);
    html += '<tr><td class="rank">' + (i+1) + '</td>'
      + '<td><a href="' + url + '" target="_blank">' + _esc(title) + '</a></td>'
      + '<td class="' + numCls + '">' + fmtK(v.view_count || v.v || 0) + '</td>'
      + '<td>' + durBadge + '</td>'
      + '<td class="muted">' + (v.publish_date || '') + '</td>'
      + '<td class="muted">' + day + '</td></tr>';
  }});
  html += '</table>';
  return html;
}}

function renderLFTables() {{
  const cutoff = _dateCutoff(_vidTimePeriod);
  let pool = ALL_VIDEOS.filter(v => (v.dur || 0) >= 5 && (v.publish_date || '') >= cutoff);

  // Apply livestream filter
  if (_vidLiveMode === 'hide') pool = pool.filter(v => !_isLive(v));
  else if (_vidLiveMode === 'only') pool = pool.filter(v => _isLive(v));

  const top20 = [...pool].sort((a,b) => (b.v||0) - (a.v||0)).slice(0, 20);
  const bot20 = [...pool].sort((a,b) => (a.v||0) - (b.v||0)).slice(0, 20);

  document.getElementById('lf-top-table').innerHTML = _renderVideoTable(top20, '');
  document.getElementById('lf-bot-table').innerHTML = _renderVideoTable(bot20, 'red');
}}

function setVidTime(period, btn) {{
  _vidTimePeriod = period;
  document.querySelectorAll('.vid-time-btn').forEach(b => {{
    b.style.background = 'var(--surface)'; b.style.color = 'var(--text-muted)'; b.style.borderColor = 'var(--border)';
  }});
  if (btn) {{ btn.style.background = 'var(--primary)'; btn.style.color = '#fff'; btn.style.borderColor = 'var(--primary)'; }}
  renderLFTables();
}}

function filterLiveRows(btn, mode) {{
  _vidLiveMode = mode;
  document.querySelectorAll('.live-filter-btn').forEach(b => {{
    b.style.background = 'var(--surface)'; b.style.color = 'var(--text-muted)'; b.style.borderColor = 'var(--border)';
  }});
  if (btn) {{ btn.style.background = 'var(--primary)'; btn.style.color = '#fff'; btn.style.borderColor = 'var(--primary)'; }}
  renderLFTables();
}}

function renderShortsTables() {{
  const cutoff = _dateCutoff(_shortsTimePeriod);
  const pool = ALL_VIDEOS.filter(v => (v.dur || 0) < 2 && (v.publish_date || '') >= cutoff);
  const top20 = [...pool].sort((a,b) => (b.v||0) - (a.v||0)).slice(0, 20);
  const bot20 = [...pool].sort((a,b) => (a.v||0) - (b.v||0)).slice(0, 20);
  document.getElementById('shorts-top-table').innerHTML = _renderVideoTable(top20, '');
  document.getElementById('shorts-bot-table').innerHTML = _renderVideoTable(bot20, 'red');
}}

function setShortsTime(period, btn) {{
  _shortsTimePeriod = period;
  document.querySelectorAll('.shorts-time-btn').forEach(b => {{
    b.style.background = 'var(--surface)'; b.style.color = 'var(--text-muted)'; b.style.borderColor = 'var(--border)';
  }});
  if (btn) {{ btn.style.background = 'var(--primary)'; btn.style.color = '#fff'; btn.style.borderColor = 'var(--primary)'; }}
  renderShortsTables();
}}

// ── Video Analysis Bot ──────────────────────────────────────────
function _vidChatBubble(role, html) {{
  const box = document.getElementById('vid-chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-bubble ' + role;
  div.innerHTML = html;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}}

function _buildVideoContext() {{
  // Build concise data summary for the AI
  const lf = ALL_VIDEOS.filter(v => (v.dur||0) >= 5);
  const shorts = ALL_VIDEOS.filter(v => (v.dur||0) < 2);
  const topLF = [...lf].sort((a,b) => (b.v||0)-(a.v||0)).slice(0,20);
  const botLF = [...lf].sort((a,b) => (a.v||0)-(b.v||0)).slice(0,20);

  // Day of week stats
  const dayStats = {{}};
  lf.forEach(v => {{
    const d = v.d || 'Unknown';
    if (!dayStats[d]) dayStats[d] = {{ total: 0, count: 0 }};
    dayStats[d].total += (v.v || 0);
    dayStats[d].count++;
  }});

  // Length bucket stats
  const lenStats = {{}};
  lf.forEach(v => {{
    const dur = v.dur || 0;
    let bucket = '';
    if (dur < 20) bucket = '5-20min';
    else if (dur < 40) bucket = '20-40min';
    else if (dur < 60) bucket = '40-60min';
    else if (dur < 90) bucket = '60-90min';
    else bucket = '90+min';
    if (!lenStats[bucket]) lenStats[bucket] = {{ total: 0, count: 0 }};
    lenStats[bucket].total += (v.v || 0);
    lenStats[bucket].count++;
  }});

  let ctx = 'CHANNEL DATA SUMMARY:\\n';
  ctx += 'Total videos: ' + ALL_VIDEOS.length + ' (' + lf.length + ' long-form, ' + shorts.length + ' shorts)\\n\\n';

  ctx += 'TOP 20 LONG-FORM BY VIEWS:\\n';
  topLF.forEach((v,i) => {{
    ctx += (i+1) + '. "' + v.title + '" - ' + (v.v||0).toLocaleString() + ' views, ' + Math.round(v.dur||0) + 'min, ' + (v.d||'') + ', ' + (v.publish_date||'') + (v.ctr ? ', CTR: ' + v.ctr + '%' : '') + '\\n';
  }});

  ctx += '\\nBOTTOM 20 LONG-FORM BY VIEWS:\\n';
  botLF.forEach((v,i) => {{
    ctx += (i+1) + '. "' + v.title + '" - ' + (v.v||0).toLocaleString() + ' views, ' + Math.round(v.dur||0) + 'min, ' + (v.d||'') + ', ' + (v.publish_date||'') + (v.ctr ? ', CTR: ' + v.ctr + '%' : '') + '\\n';
  }});

  ctx += '\\nDAY OF WEEK AVERAGES (long-form):\\n';
  ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].forEach(d => {{
    const s = dayStats[d];
    if (s && s.count) ctx += d + ': ' + Math.round(s.total/s.count).toLocaleString() + ' avg (' + s.count + ' videos)\\n';
  }});

  ctx += '\\nLENGTH BUCKET AVERAGES (long-form):\\n';
  Object.keys(lenStats).sort().forEach(b => {{
    const s = lenStats[b];
    if (s && s.count) ctx += b + ': ' + Math.round(s.total/s.count).toLocaleString() + ' avg (' + s.count + ' videos)\\n';
  }});

  ctx += '\\nKEY INSIGHTS: First-person titles ("My/I") massively outperform third-person. Sunday is the best day. 60-90 min is the best length. Cold opens beat intros. Wedding/family/AITA topics perform best.\\n';

  return ctx;
}}

async function askVideoBot() {{
  const input = document.getElementById('vid-chat-input');
  const question = input.value.trim();
  if (!question) return;
  const key = getKey();
  if (!key) {{ alert('Save your Anthropic API key in the Analytics tab first.'); return; }}

  input.value = '';
  _vidChatBubble('user', _esc(question));
  const thinking = _vidChatBubble('assistant', '<em style="color:var(--text-muted)">Analyzing your video data...</em>');

  const videoCtx = _buildVideoContext();
  const prompt = 'You are a YouTube analytics expert analyzing the OKStorytime channel. You have access to all their video data below.\\n\\n'
    + videoCtx + '\\n\\n'
    + 'USER QUESTION: ' + question + '\\n\\n'
    + 'Answer concisely with specific data points. Reference actual video titles and numbers. Use bullet points for clarity. If comparing videos, explain WHY one worked and the other didn\\'t based on title patterns, day of week, length, and topics. Keep your answer under 300 words.';

  try {{
    const resp = await fetch('https://api.anthropic.com/v1/messages', {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/json',
        'x-api-key': key,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true'
      }},
      body: JSON.stringify({{
        model: 'claude-sonnet-4-6',
        max_tokens: 800,
        messages: [{{ role: 'user', content: prompt }}]
      }})
    }});
    const data = await resp.json();
    if (data.error) {{
      thinking.innerHTML = '<span style="color:var(--red)">Error: ' + _esc(data.error.message || 'API error') + '</span>';
    }} else {{
      const text = data.content[0].text;
      thinking.innerHTML = text.replace(/\\n/g, '<br>').replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>').replace(/\\*(.+?)\\*/g, '<em>$1</em>').replace(/^- /gm, '• ');
    }}
  }} catch (e) {{
    thinking.innerHTML = '<span style="color:var(--red)">Error: ' + _esc(e.message) + '</span>';
  }}
}}

function showVideoSub(name, btn) {{
  ['longform','shorts'].forEach(n => {{
    const el = document.getElementById('video-sub-'+n);
    if (el) el.style.display = n === name ? '' : 'none';
  }});
  document.querySelectorAll('.sub-tab-btn').forEach(b => {{
    b.style.borderBottomColor = 'transparent';
    b.style.color = 'var(--text-muted)';
    b.style.fontWeight = '600';
  }});
  btn.style.borderBottomColor = 'var(--primary)';
  btn.style.color = 'var(--primary)';
  btn.style.fontWeight = '700';
}}

// ── Decline Timeline & Quick Stats tab switching ────────────────
function showDTTab(name, btn) {{
  ['longform','shorts','live'].forEach(n => {{
    const el = document.getElementById('dt-'+n);
    if (el) el.style.display = n === name ? '' : 'none';
  }});
  document.querySelectorAll('.dt-tab-btn').forEach(b => {{
    b.style.borderBottomColor = 'transparent';
    b.style.color = 'var(--text-muted)';
    b.style.fontWeight = '600';
  }});
  btn.style.borderBottomColor = 'var(--primary)';
  btn.style.color = 'var(--primary)';
  btn.style.fontWeight = '700';
}}
function showQSTab(name, btn) {{
  ['longform','shorts','live'].forEach(n => {{
    const el = document.getElementById('qs-'+n);
    if (el) el.style.display = n === name ? '' : 'none';
  }});
  document.querySelectorAll('.qs-tab-btn').forEach(b => {{
    b.style.borderBottomColor = 'transparent';
    b.style.color = 'var(--text-muted)';
    b.style.fontWeight = '600';
  }});
  btn.style.borderBottomColor = 'var(--primary)';
  btn.style.color = 'var(--primary)';
  btn.style.fontWeight = '700';
}}

// ── Launch Tracker tabs ─────────────────────────────────────────
function showLTTab(name, btn) {{
  ['7d','14d','30d'].forEach(n => {{
    const el = document.getElementById('lt-'+n);
    if (el) el.style.display = n === name ? '' : 'none';
  }});
  document.querySelectorAll('.lt-tab-btn').forEach(b => {{
    b.style.borderBottomColor = 'transparent';
    b.style.color = 'var(--text-muted)';
    b.style.fontWeight = '600';
  }});
  btn.style.borderBottomColor = 'var(--primary)';
  btn.style.color = 'var(--primary)';
  btn.style.fontWeight = '700';
}}

// ── Transcript analysis tabs ────────────────────────────────────
function showTATab(name, btn) {{
  ['findings','top','bottom'].forEach(n => {{
    const el = document.getElementById('ta-'+n);
    if (el) el.style.display = n === name ? '' : 'none';
  }});
  document.querySelectorAll('.ta-tab-btn').forEach(b => {{
    b.style.borderBottomColor = 'transparent';
    b.style.color = 'var(--text-muted)';
    b.style.fontWeight = '600';
  }});
  btn.style.borderBottomColor = 'var(--primary)';
  btn.style.color = 'var(--primary)';
  btn.style.fontWeight = '700';
}}

// ── Competitor thumbnail filter ──────────────────────────────────
let _compThumbPeriod = 'week';
let _compThumbShown = 25;
let _compThumbFiltered = [];

function filterCompThumbs(period, btn) {{
  if (btn) {{
    document.querySelectorAll('#comp-thumb-filters .filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }}
  _compThumbPeriod = period;
  _compThumbShown = 25;

  const now = new Date();
  let cutoff;
  if (period === 'week') cutoff = new Date(now - 7*24*60*60*1000);
  else if (period === 'month') cutoff = new Date(now - 30*24*60*60*1000);
  else if (period === 'year') cutoff = new Date(now - 365*24*60*60*1000);
  else cutoff = new Date(0);

  _compThumbFiltered = COMP_VIDEOS
    .filter(v => COMP_THUMBS[v.video_id] && new Date(v.published_date) >= cutoff)
    .sort((a,b) => b.view_count - a.view_count);

  renderCompThumbs();
}}

function renderCompThumbs() {{
  const grid = document.getElementById('comp-thumb-grid');
  const moreBtn = document.getElementById('comp-thumb-more');
  if (!grid) return;

  if (_compThumbFiltered.length === 0) {{
    grid.innerHTML = '<p style="color:var(--text-muted);padding:12px 0">No competitor thumbnails for this period yet. History builds daily with each refresh.</p>';
    if (moreBtn) moreBtn.style.display = 'none';
    return;
  }}

  const visible = _compThumbFiltered.slice(0, _compThumbShown);

  grid.innerHTML = '<div class="thumb-grid">' + visible.map(v => {{
    const views = v.view_count >= 1000000 ? (v.view_count/1000000).toFixed(1)+'M'
                : v.view_count >= 1000    ? Math.round(v.view_count/1000)+'K'
                : v.view_count;
    const title = v.title.length > 45 ? v.title.slice(0,45)+'…' : v.title;
    return '<div class="thumb-item">'
      + '<a href="'+v.url+'" target="_blank">'
      + '<img src="data:image/jpeg;base64,'+COMP_THUMBS[v.video_id]+'" alt="'+title+'" loading="lazy"></a>'
      + '<div class="thumb-label">'
      + '<span class="thumb-dur">'+v.channel+'</span>'
      + '<strong style="font-size:.78rem;color:var(--primary)">'+views+' views</strong>'
      + '<strong style="font-size:.8rem;margin-top:3px;display:block">'+title+'</strong>'
      + '<span style="font-size:.7rem;color:var(--text-muted)">'+v.published+'</span>'
      + '</div></div>';
  }}).join('') + '</div>';

  if (moreBtn) {{
    const remaining = _compThumbFiltered.length - _compThumbShown;
    if (remaining > 0) {{
      moreBtn.style.display = 'block';
      moreBtn.textContent = 'Show 25 More (' + remaining + ' remaining)';
    }} else {{
      moreBtn.style.display = 'none';
    }}
  }}
}}

function showMoreCompThumbs() {{
  _compThumbShown += 25;
  renderCompThumbs();
}}
window.showMoreCompThumbs = showMoreCompThumbs;

// ── Initialize ──────────────────────────────────────────────────
filterAnalytics('all', document.getElementById('fb-all'));
redrawChart();
filterThumbs('all', null);
filterCompThumbs('week', null);
filterTitles('all', null);
renderLFTables();
renderShortsTables();

// ── AI Chat ─────────────────────────────────────────────────────
(function() {{
  const KEY_LS = 'okst_claude_key';

  function saveKey() {{
    const v = document.getElementById('api-key-input').value.trim();
    if (!v) return;
    localStorage.setItem(KEY_LS, v);
    document.getElementById('api-key-input').value = '';
    document.getElementById('key-bar').style.display = 'none';
    document.getElementById('key-saved').style.display = 'flex';
  }}
  function clearKey() {{
    localStorage.removeItem(KEY_LS);
    document.getElementById('api-key-input').value = '';
    document.getElementById('key-bar').style.display = 'flex';
    document.getElementById('key-saved').style.display = 'none';
  }}
  function getKey() {{ return localStorage.getItem(KEY_LS); }}

  // Show saved indicator on load
  window.addEventListener('DOMContentLoaded', () => {{
    if (getKey()) {{
      document.getElementById('key-bar').style.display = 'none';
      document.getElementById('key-saved').style.display = 'flex';
    }}
  }});

  function sanitize(s) {{ return String(s).replace(/`/g, "'").replace(/[$]/g, ''); }}
  function buildSystemPrompt() {{
    const rows = ALL_VIDEOS.slice(0,500).map(v =>
      v.publish_date+'|'+v.duration_minutes+'min|'+v.view_count+'views|'+sanitize(v.title)
    ).join('\\n');
    const monthly = MONTHLY_DATA.map(m => m.month+': '+m.avg_views+' avg views, '+m.watch_hours+'h watch time, '+m.ctr+'% CTR ('+m.count+' videos)').join(', ');
    return 'You are an expert YouTube analytics advisor for the channel OKStorytime (OKOPShow).\\n'
      + 'You have access to their full video data. Use it to give specific, data-backed answers.\\n\\n'
      + 'MONTHLY TREND (avg views per video):\\n' + monthly + '\\n\\n'
      + 'TOP 500 VIDEOS (date|duration|views|title):\\n' + rows + '\\n\\n'
      + 'When the user asks why views dropped, reference specific months and videos. Be direct, concise, and actionable.';
  }}

  async function sendChat() {{
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;
    const key = getKey();
    if (!key) {{
      appendBubble('assistant', '⚠️ Please save your Anthropic API key first using the field above.');
      return;
    }}
    input.value = '';
    appendBubble('user', msg);
    const thinkEl = appendBubble('assistant', '<span class="thinking-dot"></span> Thinking…');
    document.getElementById('chat-send').disabled = true;

    try {{
      const resp = await fetch('https://api.anthropic.com/v1/messages', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          'x-api-key': key,
          'anthropic-version': '2023-06-01',
          'anthropic-dangerous-direct-browser-access': 'true'
        }},
        body: JSON.stringify({{
          model: 'claude-opus-4-6',
          max_tokens: 1024,
          system: buildSystemPrompt(),
          messages: [{{ role: 'user', content: msg }}]
        }})
      }});
      const data = await resp.json();
      if (data.error) {{
        thinkEl.innerHTML = '⚠️ API error: ' + (data.error.message || JSON.stringify(data.error));
      }} else {{
        const text = data.content?.[0]?.text || '(no response)';
        thinkEl.innerHTML = text.replace(/\\n/g, '<br>');
      }}
    }} catch(e) {{
      thinkEl.innerHTML = '⚠️ Request failed: ' + e.message;
    }}
    document.getElementById('chat-send').disabled = false;
  }}

  function askChip(el) {{
    document.getElementById('chat-input').value = el.textContent;
    sendChat();
  }}

  function appendBubble(role, html) {{
    const box = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'chat-bubble ' + role;
    div.innerHTML = html;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }}

  // ── Title Generator ──────────────────────────────────────────
  async function generateTitles() {{
    const story = document.getElementById('story-input').value.trim();
    if (!story) {{ alert('Paste a story summary first.'); return; }}
    const key = getKey();
    if (!key) {{ alert('Save your Anthropic API key in the Analytics tab first.'); return; }}

    const btn = document.getElementById('gen-btn');
    const status = document.getElementById('gen-status');
    const results = document.getElementById('title-results');
    const list = document.getElementById('title-list');
    btn.disabled = true;
    status.textContent = 'Generating…';
    results.style.display = 'none';

    const topWords = ['truth','dark','reaction','hours','proposed','abandoned','secret','demanded','exposed','wedding'];
    const avoidWords = ['clip','tifu','maliciouscompliance','askreddit','full','flag','denise','brady','joanna'];
    const exampleTitles = [
      'My husband BONED his co-worker…',
      'I was accused of cheating… the DNA Test revealed the TRUTH',
      'Stepdad wants me to pay rent\u2026 but he doesn\u2019t know my secret',
      'She abandoned her kids for a new life — then begged to come back',
      'My boss demanded I work on my wedding day… so I quit live on Zoom'
    ];

    const prompt = 'You are a YouTube title expert for the channel OKStorytime — a reddit story reaction channel.\\n\\n'
      + 'PROVEN TITLE RULES for this channel:\\n'
      + '- Start with first-person drama or shocking action (not the host name, not a subreddit tag)\\n'
      + '- Must create unresolved tension — viewer NEEDS to know what happens\\n'
      + '- Use strong emotional words: TRUTH, SECRET, REVEALED, BONED, ABANDONED, DEMANDED, EXPOSED\\n'
      + '- Ellipsis (…) works well to create a cliffhanger mid-title\\n'
      + '- Best performing words to include if natural: truth, dark, reaction, hours, proposed, abandoned, secret, demanded, exposed\\n'
      + '- AVOID: clip, tifu, maliciouscompliance, askreddit, full, flag, host names (Denise, Brady, Joanna)\\n'
      + '- AVOID leading with: "r/", "rSlash", host names, or "full episode"\\n'
      + '- Length: 60-80 characters is ideal\\n\\n'
      + 'TOP PERFORMING TITLE EXAMPLES from this channel:\\n'
      + exampleTitles.map((t,i) => (i+1)+'. '+t).join('\\n') + '\\n\\n'
      + 'STORY TO TITLE:\\n' + sanitize(story) + '\\n\\n'
      + 'Generate 8 YouTube title options. Number them 1-8. Each title on its own line. No extra commentary — just the numbered list.';

    try {{
      const resp = await fetch('https://api.anthropic.com/v1/messages', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          'x-api-key': key,
          'anthropic-version': '2023-06-01',
          'anthropic-dangerous-direct-browser-access': 'true'
        }},
        body: JSON.stringify({{
          model: 'claude-opus-4-6',
          max_tokens: 600,
          messages: [{{ role: 'user', content: prompt }}]
        }})
      }});
      const data = await resp.json();
      if (data.error) {{
        status.textContent = '⚠️ ' + (data.error.message || 'API error');
      }} else {{
        const text = data.content?.[0]?.text || '';
        const lines = text.split('\\n').map(l => l.replace(/^\\d+[.)\\s]+/, '').trim()).filter(l => l.length > 5);
        list.innerHTML = lines.map(l => `<li style="padding:6px 0;border-bottom:1px solid var(--border)">${{l}}</li>`).join('');
        results.style.display = 'block';
        status.textContent = `${{lines.length}} titles generated`;
      }}
    }} catch(e) {{
      status.textContent = '⚠️ ' + e.message;
    }}
    btn.disabled = false;
  }}

  // Expose to global scope
  window.saveKey = saveKey;
  window.clearKey = clearKey;
  window.getKey = getKey;
  window.sendChat = sendChat;
  window.askChip = askChip;
  window.generateTitles = generateTitles;
}})();

// ── CSV Upload & Studio Analytics ────────────────────────────
function parseCSVLine(line) {{
  const result = []; let cur = ''; let inQuotes = false;
  for (let i = 0; i < line.length; i++) {{
    const ch = line[i];
    if (ch === '"') {{ inQuotes = !inQuotes; }}
    else if (ch === ',' && !inQuotes) {{ result.push(cur.trim()); cur = ''; }}
    else {{ cur += ch; }}
  }}
  result.push(cur.trim());
  return result;
}}

function parseCSV(text) {{
  const lines = text.split(/\\r?\\n/).filter(l => l.trim());
  if (lines.length < 2) return [];
  const headers = parseCSVLine(lines[0]);
  const rows = [];
  for (let i = 1; i < lines.length; i++) {{
    const vals = parseCSVLine(lines[i]);
    const obj = {{}};
    headers.forEach((h, j) => obj[h] = vals[j] || '');
    rows.push(obj);
  }}
  return rows;
}}

function parseNum(v) {{
  if (!v) return 0;
  return parseFloat(v.replace(/[,$%]/g, '')) || 0;
}}

function parseDuration(s) {{
  if (!s) return 0;
  const parts = s.split(':').map(Number);
  if (parts.length === 3) return parts[0] * 60 + parts[1] + parts[2] / 60;
  if (parts.length === 2) return parts[0] + parts[1] / 60;
  return parts[0] / 60;
}}

let studioChart = null;

function handleCSVDrop(files) {{
  const fileList = document.getElementById('upload-file-list');
  const status = document.getElementById('upload-status');
  fileList.innerHTML = '';
  status.innerHTML = '';

  const fileMap = {{}};
  Array.from(files).forEach(f => {{
    const name = f.name;
    fileMap[name] = f;
    const tag = document.createElement('span');
    tag.className = 'upload-file-tag pending';
    tag.textContent = name;
    tag.id = 'ftag-' + name.replace(/[^a-zA-Z0-9]/g, '_');
    fileList.appendChild(tag);
  }});

  const readers = Array.from(files).map(f => {{
    return new Promise((resolve) => {{
      const reader = new FileReader();
      reader.onload = () => resolve({{ name: f.name, text: reader.result }});
      reader.readAsText(f);
    }});
  }});

  Promise.all(readers).then(results => {{
    let tableData = null, chartData = null, totalsData = null;

    results.forEach(r => {{
      const tag = document.getElementById('ftag-' + r.name.replace(/[^a-zA-Z0-9]/g, '_'));
      const rows = parseCSV(r.text);

      if (r.name.toLowerCase().includes('table') && rows.length > 0 && rows[0]['Video title']) {{
        tableData = rows.filter(row => row['Content'] !== 'Total');
        if (tag) {{ tag.className = 'upload-file-tag'; tag.textContent = r.name + ' (' + tableData.length + ' videos)'; }}
      }} else if (r.name.toLowerCase().includes('chart') && rows.length > 0 && rows[0]['Content']) {{
        chartData = rows;
        if (tag) {{ tag.className = 'upload-file-tag'; tag.textContent = r.name + ' (' + rows.length + ' rows)'; }}
      }} else if (r.name.toLowerCase().includes('total')) {{
        totalsData = rows;
        if (tag) {{ tag.className = 'upload-file-tag'; tag.textContent = r.name + ' (' + rows.length + ' rows)'; }}
      }} else {{
        if (tag) {{ tag.className = 'upload-file-tag'; tag.textContent = r.name + ' (auto-detected)'; }}
        // Try to auto-detect
        if (rows.length > 0) {{
          if (rows[0]['Video title'] && rows[0]['Impressions']) {{ tableData = rows.filter(row => row['Content'] !== 'Total'); }}
          else if (rows[0]['Content'] && rows[0]['Engaged views'] && rows[0]['Date']) {{ chartData = rows; }}
          else if (rows[0]['Date'] && rows[0]['Engaged views'] && !rows[0]['Content']) {{ totalsData = rows; }}
        }}
      }}
    }});

    if (tableData) {{
      renderStudioTable(tableData);
      // Save to localStorage
      localStorage.setItem('studio_table_data', JSON.stringify(tableData));
    }}
    if (totalsData) {{
      renderMonthlyChart(totalsData);
      localStorage.setItem('studio_totals_data', JSON.stringify(totalsData));
    }}

    const loaded = [tableData ? 'Table data' : null, chartData ? 'Chart data' : null, totalsData ? 'Totals' : null].filter(Boolean);
    if (loaded.length > 0) {{
      status.innerHTML = '<div class="upload-status success">Loaded: ' + loaded.join(', ') + '. Data saved in your browser for next visit.</div>';
      document.getElementById('studio-results').classList.add('visible');
    }} else {{
      status.innerHTML = '<div class="upload-status error">Could not detect YouTube Studio CSV format. Make sure you export from Advanced Mode.</div>';
    }}
  }});
}}

function renderStudioTable(rows) {{
  // Parse and filter to long-form (5+ min)
  const videos = rows.map(r => ({{
    title: r['Video title'] || '',
    video_id: r['Content'] || '',
    views: parseNum(r['Views']),
    impressions: parseNum(r['Impressions']),
    ctr: parseNum(r['Impressions click-through rate (%)']),
    avg_dur: parseDuration(r['Average view duration']),
    avg_pct: parseNum(r['Average percentage viewed (%)']),
    revenue: parseNum(r['Estimated revenue (USD)']),
    rpm: parseNum(r['RPM (USD)']),
    duration_sec: parseNum(r['Duration']),
    duration_min: parseNum(r['Duration']) / 60,
  }})).filter(v => v.title);

  const longform = videos.filter(v => v.duration_min >= 5);
  const withCtr = longform.filter(v => v.ctr > 0);
  const avgCtr = withCtr.length ? (withCtr.reduce((s, v) => s + v.ctr, 0) / withCtr.length).toFixed(1) : '0';
  const totalRev = videos.reduce((s, v) => s + v.revenue, 0);
  const totalImpr = videos.reduce((s, v) => s + v.impressions, 0);
  const totalHours = rows.reduce((s, r) => s + parseNum(r['Watch time (hours)']), 0);
  const avgRpm = withCtr.length ? (withCtr.reduce((s, v) => s + v.rpm, 0) / withCtr.length).toFixed(2) : '0';

  // Summary line
  document.getElementById('studio-summary-line').innerHTML =
    withCtr.length + ' long-form videos with CTR data. ' +
    'Avg CTR: <strong>' + avgCtr + '%</strong> · ' +
    'Total Revenue: <strong>$' + totalRev.toLocaleString('en-US', {{maximumFractionDigits: 0}}) + '</strong> · ' +
    'Total Impressions: <strong>' + totalImpr.toLocaleString() + '</strong> · ' +
    'Total Watch Hours: <strong>' + Math.round(totalHours).toLocaleString() + '</strong>';

  // Top 25 by revenue
  const topByRev = [...longform].sort((a, b) => b.revenue - a.revenue).slice(0, 25);
  const tbody = topByRev.map(v => {{
    const ctrCls = v.ctr >= 8 ? 'green' : (v.ctr < 4 ? 'red' : '');
    const revCls = v.revenue >= 2000 ? 'green' : '';
    const tShort = v.title.length > 55 ? v.title.slice(0, 55) + '...' : v.title;
    return '<tr>' +
      '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + v.title.replace(/"/g, '&quot;') + '">' + tShort + '</td>' +
      '<td class="num">' + v.views.toLocaleString() + '</td>' +
      '<td class="num">' + v.impressions.toLocaleString() + '</td>' +
      '<td class="num ' + ctrCls + '">' + v.ctr.toFixed(1) + '%</td>' +
      '<td class="num">' + Math.round(v.avg_dur) + 'm</td>' +
      '<td class="num">' + Math.round(v.avg_pct) + '%</td>' +
      '<td class="num ' + revCls + '">$' + Math.round(v.revenue).toLocaleString() + '</td>' +
      '<td class="num">$' + v.rpm.toFixed(2) + '</td></tr>';
  }}).join('');

  const table = document.getElementById('studio-table');
  table.innerHTML = '<tr><th>Title</th><th>Views</th><th>Impressions</th><th>CTR</th><th>Avg Duration</th><th>Avg % Viewed</th><th>Revenue</th><th>RPM</th></tr>' + tbody;

  // CTR buckets
  const buckets = [[' < 3%', 0, 3], ['3-5%', 3, 5], ['5-8%', 5, 8], ['8-12%', 8, 12], ['12%+', 12, 100]];
  const ctrRows = buckets.map(([label, lo, hi]) => {{
    const bv = withCtr.filter(v => v.ctr >= lo && v.ctr < hi);
    if (!bv.length) return '';
    const avgV = Math.round(bv.reduce((s, v) => s + v.views, 0) / bv.length);
    const avgR = Math.round(bv.reduce((s, v) => s + v.revenue, 0) / bv.length);
    return '<tr><td>' + label + '</td><td class="num">' + bv.length + '</td><td class="num">' + avgV.toLocaleString() + '</td><td class="num">$' + avgR.toLocaleString() + '</td></tr>';
  }}).join('');
  document.getElementById('studio-ctr-table').innerHTML = '<tr><th>CTR Range</th><th>Videos</th><th>Avg Views</th><th>Avg Revenue</th></tr>' + ctrRows;

  // Key metrics
  document.getElementById('studio-metrics-table').innerHTML =
    '<tr><td>Avg CTR (long-form)</td><td class="num">' + avgCtr + '%</td></tr>' +
    '<tr><td>Total Revenue</td><td class="num">$' + Math.round(totalRev).toLocaleString() + '</td></tr>' +
    '<tr><td>Total Impressions</td><td class="num">' + totalImpr.toLocaleString() + '</td></tr>' +
    '<tr><td>Total Watch Hours</td><td class="num">' + Math.round(totalHours).toLocaleString() + '</td></tr>' +
    '<tr><td>Avg Revenue / Video</td><td class="num">$' + Math.round(totalRev / Math.max(longform.length, 1)).toLocaleString() + '</td></tr>' +
    '<tr><td>Avg RPM (long-form)</td><td class="num">$' + avgRpm + '</td></tr>';

  // Update header stats
  const headerStats = document.querySelector('.header-stats');
  if (headerStats) {{
    // Remove old studio stats if any
    headerStats.querySelectorAll('.studio-stat').forEach(el => el.remove());
    headerStats.innerHTML += '<div class="hstat studio-stat"><div class="val">' + avgCtr + '%</div><div class="lbl">Avg CTR (LF)</div></div>';
    headerStats.innerHTML += '<div class="hstat studio-stat"><div class="val">$' + Math.round(totalRev).toLocaleString() + '</div><div class="lbl">Total Revenue</div></div>';
  }}
}}

function renderMonthlyChart(rows) {{
  const monthly = {{}};
  rows.forEach(r => {{
    const date = r['Date'] || '';
    const views = parseNum(r['Engaged views']);
    if (date) {{
      const month = date.slice(0, 7);
      monthly[month] = (monthly[month] || 0) + views;
    }}
  }});

  const labels = Object.keys(monthly).sort();
  const data = labels.map(k => monthly[k]);

  const canvas = document.getElementById('studio-monthly-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  if (studioChart) studioChart.destroy();
  if (typeof Chart !== 'undefined') {{
    studioChart = new Chart(ctx, {{
      type: 'bar',
      data: {{
        labels: labels.map(l => {{
          const [y, m] = l.split('-');
          return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][parseInt(m)-1] + ' ' + y;
        }}),
        datasets: [{{ label: 'Engaged Views', data: data, backgroundColor: 'rgba(124,58,237,.5)', borderColor: 'rgba(124,58,237,1)', borderWidth: 1 }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{ y: {{ beginAtZero: true, ticks: {{ callback: v => v >= 1000000 ? (v/1000000).toFixed(1)+'M' : v >= 1000 ? (v/1000).toFixed(0)+'K' : v }} }} }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});
  }}
}}

// Load saved studio data on page load
window.addEventListener('DOMContentLoaded', () => {{
  const saved = localStorage.getItem('studio_table_data');
  const savedTotals = localStorage.getItem('studio_totals_data');
  if (saved) {{
    try {{
      renderStudioTable(JSON.parse(saved));
      document.getElementById('studio-results').classList.add('visible');
    }} catch(e) {{}}
  }}
  if (savedTotals) {{
    try {{ renderMonthlyChart(JSON.parse(savedTotals)); }} catch(e) {{}}
  }}
}});

window.handleCSVDrop = handleCSVDrop;

// ── Competitor Titles (filtered + paginated) ──────────────────
let _compTitlePeriod = '7d';
let _compTitleShown = 25;
let _compTitleFiltered = [];

function filterCompTitles(period, btn) {{
  if (btn) {{
    document.querySelectorAll('#comp-title-filters .filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }}
  _compTitlePeriod = period;
  _compTitleShown = 25;

  const now = new Date();
  let pool = [...COMP_ALL];

  if (period === '7d') {{
    const cut = new Date(now - 7*86400000).toISOString().slice(0,10);
    pool = pool.filter(v => v.published_date >= cut);
  }} else if (period === '30d') {{
    const cut = new Date(now - 30*86400000).toISOString().slice(0,10);
    pool = pool.filter(v => v.published_date >= cut);
  }} else if (period === 'year') {{
    const yr = String(now.getFullYear());
    pool = pool.filter(v => v.published_date && v.published_date.startsWith(yr));
  }}

  _compTitleFiltered = pool.sort((a,b) => (b.view_count||0) - (a.view_count||0));
  renderCompTitles();
}}

function renderCompTitles() {{
  const list = document.getElementById('comp-title-list');
  const moreBtn = document.getElementById('comp-title-more');
  if (!list) return;

  const visible = _compTitleFiltered.slice(0, _compTitleShown);
  if (visible.length === 0) {{
    list.innerHTML = '<p style="color:var(--text-muted);font-size:.85rem">No competitor videos found for this period.</p>';
    moreBtn.style.display = 'none';
    return;
  }}

  list.innerHTML = visible.map(v => {{
    const vc = v.view_count || 0;
    const views = vc >= 1000000 ? (vc/1000000).toFixed(1)+'M views'
                : vc >= 1000 ? Math.round(vc/1000)+'K views'
                : vc > 0 ? vc+' views' : '';
    return '<div style="display:flex;align-items:baseline;gap:8px;padding:7px 10px;border-radius:8px;background:var(--surface2);border:1px solid var(--border)">'
      + '<span class="thumb-dur" style="flex-shrink:0">' + _esc(v.channel) + '</span>'
      + '<a href="' + v.url + '" target="_blank" style="font-size:.875rem;font-weight:600;color:var(--text);text-decoration:none;flex:1">' + _esc(v.title) + '</a>'
      + (views ? '<span style="font-size:.72rem;color:var(--text-muted);margin-left:6px">' + views + '</span>' : '')
      + '<span style="font-size:.72rem;color:var(--text-muted);flex-shrink:0">' + (v.published || '') + '</span>'
      + '</div>';
  }}).join('');

  const remaining = _compTitleFiltered.length - _compTitleShown;
  if (remaining > 0) {{
    moreBtn.style.display = 'block';
    moreBtn.textContent = 'Show 25 More (' + remaining + ' remaining)';
  }} else {{
    moreBtn.style.display = 'none';
  }}
}}

function showMoreCompTitles() {{
  _compTitleShown += 25;
  renderCompTitles();
}}
window.filterCompTitles = filterCompTitles;
window.showMoreCompTitles = showMoreCompTitles;

// ── Title Pattern Analyzer ────────────────────────────────────
function analyzeTitlePatterns() {{
  const lf = ALL_VIDEOS.filter(v => v.duration_minutes >= 5 && v.view_count > 0);
  if (lf.length < 10) return;

  const patterns = [
    {{
      name: 'First-Person ("I/My") vs Third-Person ("She/He/They")',
      testA: v => /^(I |My |I\'m |I\'ve )/i.test(v.title),
      labelA: 'First-person (I/My...)',
      testB: v => /^(She |He |They |Her |His )/i.test(v.title),
      labelB: 'Third-person (She/He...)',
    }},
    {{
      name: 'With "Reddit" vs Without',
      testA: v => /reddit/i.test(v.title),
      labelA: 'Has "Reddit" in title',
      testB: v => !/reddit/i.test(v.title),
      labelB: 'No "Reddit" in title',
    }},
    {{
      name: 'Question Title vs Statement',
      testA: v => /\?/.test(v.title),
      labelA: 'Question (has ?)',
      testB: v => !/\?/.test(v.title) && !/\|/.test(v.title),
      labelB: 'Statement (no ?)',
    }},
    {{
      name: 'Has Ellipsis/Cliffhanger (...) vs Clean End',
      testA: v => /\.\.\.|\u2026/.test(v.title),
      labelA: 'Has ... (cliffhanger)',
      testB: v => !/\.\.\.|\u2026/.test(v.title),
      labelB: 'Clean ending',
    }},
    {{
      name: 'Has Pipe Separator (|) vs No Separator',
      testA: v => /\|/.test(v.title),
      labelA: 'Has | separator',
      testB: v => !/\|/.test(v.title),
      labelB: 'No separator',
    }},
    {{
      name: 'ALL CAPS Word vs No Caps',
      testA: v => /\b[A-Z]{{3,}}\b/.test(v.title),
      labelA: 'Has CAPS word',
      testB: v => !/\b[A-Z]{{3,}}\b/.test(v.title),
      labelB: 'No caps word',
    }},
  ];

  const container = document.getElementById('title-patterns');
  if (!container) return;

  container.innerHTML = patterns.map(p => {{
    const groupA = lf.filter(p.testA);
    const groupB = lf.filter(p.testB);
    if (groupA.length < 3 || groupB.length < 3) return '';

    const avgA = Math.round(groupA.reduce((s,v) => s + v.view_count, 0) / groupA.length);
    const avgB = Math.round(groupB.reduce((s,v) => s + v.view_count, 0) / groupB.length);
    const ctrA = groupA.filter(v=>v.ctr>0);
    const ctrB = groupB.filter(v=>v.ctr>0);
    const avgCtrA = ctrA.length ? (ctrA.reduce((s,v)=>s+v.ctr,0)/ctrA.length).toFixed(1) : 'N/A';
    const avgCtrB = ctrB.length ? (ctrB.reduce((s,v)=>s+v.ctr,0)/ctrB.length).toFixed(1) : 'N/A';
    const winner = avgA > avgB ? 'A' : 'B';
    const pct = Math.round(Math.abs(avgA - avgB) / Math.min(avgA, avgB) * 100);

    return '<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px">'
      + '<div style="font-weight:700;font-size:.85rem;margin-bottom:10px">' + p.name + '</div>'
      + '<table style="width:100%;font-size:.82rem"><tr><th></th><th>Videos</th><th>Avg Views</th><th>Avg CTR</th></tr>'
      + '<tr style="'+(winner==='A'?'background:#f0fdf4':'')+'">'
      + '<td>'+(winner==='A'?'✅ ':'')+p.labelA+'</td>'
      + '<td class="num">'+groupA.length+'</td><td class="num">'+avgA.toLocaleString()+'</td><td class="num">'+avgCtrA+'%</td></tr>'
      + '<tr style="'+(winner==='B'?'background:#f0fdf4':'')+'">'
      + '<td>'+(winner==='B'?'✅ ':'')+p.labelB+'</td>'
      + '<td class="num">'+groupB.length+'</td><td class="num">'+avgB.toLocaleString()+'</td><td class="num">'+avgCtrB+'%</td></tr>'
      + '</table>'
      + '<div style="font-size:.78rem;color:var(--primary);font-weight:600;margin-top:8px">'
      + (winner==='A'?p.labelA:p.labelB) + ' wins by ' + pct + '% more views</div></div>';
  }}).join('');
}}

// ── 10K/48hr Scorecard ────────────────────────────────────────
function scoreVideo() {{
  const title = document.getElementById('sc-title').value;
  const host = document.getElementById('sc-host').value;
  const length = document.getElementById('sc-length').value;
  const day = document.getElementById('sc-day').value;
  const thumb = document.getElementById('sc-thumb').value;
  const firstPerson = document.getElementById('sc-first-person').checked;
  const twist = document.getElementById('sc-twist').checked;
  const relationship = document.getElementById('sc-relationship').checked;

  let score = 0;
  const breakdown = [];

  // Host (from data: Sam/John solo performs best)
  if (host === 'sam' || host === 'john') {{ score += 20; breakdown.push('+20 Sam or John solo (proven top performers)'); }}
  else if (host === 'both') {{ score += 15; breakdown.push('+15 Sam + John (good but solo often better)'); }}
  else {{ score += 5; breakdown.push('+5 Guest episode (Sam says no guests on Wednesday)'); }}

  // Length
  if (length === '60-90') {{ score += 20; breakdown.push('+20 60-90 min (your best-performing length)'); }}
  else if (length === '40-60') {{ score += 15; breakdown.push('+15 40-60 min (solid length)'); }}
  else if (length === '20-40') {{ score += 10; breakdown.push('+10 20-40 min (decent)'); }}
  else {{ score += 5; breakdown.push('+5 Other length'); }}

  // Day
  if (day === 'Sunday') {{ score += 15; breakdown.push('+15 Sunday (your best day)'); }}
  else if (day === 'Wednesday' || day === 'Saturday') {{ score += 10; breakdown.push('+10 ' + day + ' (good day)'); }}
  else {{ score += 5; breakdown.push('+5 ' + day); }}

  // Thumbnail style
  if (thumb === 'closeup') {{ score += 15; breakdown.push('+15 Close-up face (highest CTR style)'); }}
  else if (thumb === 'studio') {{ score += 10; breakdown.push('+10 Studio shot'); }}
  else if (thumb === 'karen') {{ score += 8; breakdown.push('+8 Karen style (test this!)'); }}
  else {{ score += 3; breakdown.push('+3 ' + thumb + ' (not ideal)'); }}

  // Title patterns
  if (firstPerson) {{ score += 10; breakdown.push('+10 First-person title (I/My)'); }}
  else {{ score += 3; breakdown.push('+3 Not first-person'); }}
  if (twist) {{ score += 10; breakdown.push('+10 Unresolved twist (creates curiosity gap)'); }}
  if (relationship) {{ score += 10; breakdown.push('+10 Relationship story (your audience loves these)'); }}

  // Cap at 100
  score = Math.min(score, 100);

  const display = document.getElementById('score-display');
  const cls = score >= 75 ? 'score-high' : score >= 50 ? 'score-mid' : 'score-low';
  display.innerHTML = '<div class="score-ring ' + cls + '" style="font-size:1.4rem;width:70px;height:70px">' + score + '</div>'
    + '<div style="font-size:.75rem;color:var(--text-muted);margin-top:6px">'
    + (score >= 75 ? 'Strong candidate for 10K!' : score >= 50 ? 'Has potential — optimize weak areas' : 'Needs work before launch')
    + '</div>';

  const bd = document.getElementById('score-breakdown');
  bd.style.display = 'block';
  bd.innerHTML = '<strong>Score breakdown for: ' + (title || 'Untitled') + '</strong><br><br>'
    + breakdown.map(b => '• ' + b).join('<br>')
    + '<br><br><strong style="color:var(--primary)">Total: ' + score + '/100</strong>'
    + (score < 75 ? '<br><span style="color:var(--text-muted);font-size:.82rem">Tip: Optimize the lowest-scoring areas before publishing.</span>' : '');
}}
window.scoreVideo = scoreVideo;

// ── Experiment Tracker (localStorage) ─────────────────────────
function getExperiments() {{
  try {{ return JSON.parse(localStorage.getItem('okst_experiments') || '[]'); }}
  catch(e) {{ return []; }}
}}
function saveExperiments(exps) {{ localStorage.setItem('okst_experiments', JSON.stringify(exps)); }}

function addExperiment() {{
  const video = document.getElementById('exp-video-url').value.trim();
  const type = document.getElementById('exp-type').value;
  const hypothesis = document.getElementById('exp-hypothesis').value.trim();
  const varA = document.getElementById('exp-variant-a').value.trim();
  const varB = document.getElementById('exp-variant-b').value.trim();
  const startDate = document.getElementById('exp-start-date').value || new Date().toISOString().slice(0,10);

  if (!video || !hypothesis) {{ alert('Fill in the video and hypothesis.'); return; }}

  const exps = getExperiments();
  exps.push({{
    id: Date.now(),
    video, type, hypothesis, varA, varB,
    startDate,
    status: 'active',
    result: '',
    winner: '',
    createdAt: new Date().toISOString()
  }});
  saveExperiments(exps);

  // Clear form
  document.getElementById('exp-video-url').value = '';
  document.getElementById('exp-hypothesis').value = '';
  document.getElementById('exp-variant-a').value = '';
  document.getElementById('exp-variant-b').value = '';

  renderExperiments();
}}

function completeExperiment(id) {{
  const exps = getExperiments();
  const exp = exps.find(e => e.id === id);
  if (!exp) return;

  const winner = prompt('Which variant won? (A or B)');
  if (!winner) return;
  const result = prompt('What did you learn? (Brief result)');

  exp.status = 'completed';
  exp.winner = winner.toUpperCase();
  exp.result = result || '';
  exp.completedAt = new Date().toISOString();
  saveExperiments(exps);
  renderExperiments();
}}

function deleteExperiment(id) {{
  if (!confirm('Delete this experiment?')) return;
  const exps = getExperiments().filter(e => e.id !== id);
  saveExperiments(exps);
  renderExperiments();
}}

function renderExperiments() {{
  const exps = getExperiments();
  const active = exps.filter(e => e.status === 'active');
  const completed = exps.filter(e => e.status === 'completed');

  const activeEl = document.getElementById('exp-list');
  const completedEl = document.getElementById('exp-completed');
  if (!activeEl || !completedEl) return;

  if (active.length === 0) {{
    activeEl.innerHTML = '<p style="color:var(--text-muted);font-size:.85rem">No active experiments. Start one above.</p>';
  }} else {{
    activeEl.innerHTML = active.map(e => '<div class="exp-card">'
      + '<div class="exp-title">' + _esc(e.video) + ' <span style="background:var(--primary-bg);color:var(--primary);padding:2px 8px;border-radius:6px;font-size:.72rem;font-weight:700">' + e.type + '</span></div>'
      + '<div class="exp-meta">Started: ' + e.startDate + '</div>'
      + '<div class="exp-hyp">' + _esc(e.hypothesis) + '</div>'
      + (e.varA ? '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;font-size:.82rem"><div style="padding:8px;background:var(--surface2);border-radius:6px"><strong>A:</strong> ' + _esc(e.varA) + '</div><div style="padding:8px;background:var(--surface2);border-radius:6px"><strong>B:</strong> ' + _esc(e.varB) + '</div></div>' : '')
      + '<div style="display:flex;gap:8px;margin-top:10px">'
      + '<button onclick="completeExperiment(' + e.id + ')" style="background:var(--green);color:#fff;border:none;border-radius:6px;padding:5px 14px;font-size:.8rem;cursor:pointer;font-weight:600">Complete & Log Result</button>'
      + '<button onclick="deleteExperiment(' + e.id + ')" style="background:none;border:1px solid var(--border);border-radius:6px;padding:5px 14px;font-size:.8rem;cursor:pointer;color:var(--text-muted)">Delete</button>'
      + '</div></div>').join('');
  }}

  if (completed.length === 0) {{
    completedEl.innerHTML = '<p style="color:var(--text-muted);font-size:.85rem">No completed experiments yet.</p>';
  }} else {{
    completedEl.innerHTML = completed.map(e => '<div class="exp-card" style="border-left:3px solid var(--green)">'
      + '<div class="exp-title">' + _esc(e.video) + ' <span style="background:#dcfce7;color:#166534;padding:2px 8px;border-radius:6px;font-size:.72rem;font-weight:700">COMPLETED</span> <span style="background:var(--primary-bg);color:var(--primary);padding:2px 8px;border-radius:6px;font-size:.72rem;font-weight:700">' + e.type + '</span></div>'
      + '<div class="exp-meta">Ran: ' + e.startDate + ' → ' + (e.completedAt||'').slice(0,10) + '</div>'
      + '<div class="exp-hyp">' + _esc(e.hypothesis) + '</div>'
      + '<div style="margin-top:8px;font-size:.85rem"><strong>Winner: Variant ' + e.winner + '</strong></div>'
      + (e.result ? '<div style="margin-top:4px;font-size:.85rem;color:var(--text-muted)">' + _esc(e.result) + '</div>' : '')
      + '<div style="margin-top:8px"><button onclick="deleteExperiment(' + e.id + ')" style="background:none;border:1px solid var(--border);border-radius:6px;padding:4px 12px;font-size:.78rem;cursor:pointer;color:var(--text-muted)">Delete</button></div>'
      + '</div>').join('');
  }}
}}
window.addExperiment = addExperiment;
window.completeExperiment = completeExperiment;
window.deleteExperiment = deleteExperiment;

// ── Rules Tracker (localStorage) ──────────────────────────────
function getRules() {{
  try {{ return JSON.parse(localStorage.getItem('okst_rules') || '[]'); }}
  catch(e) {{ return []; }}
}}
function saveRules(rules) {{ localStorage.setItem('okst_rules', JSON.stringify(rules)); }}

function addRule() {{
  const input = document.getElementById('new-rule');
  const text = input.value.trim();
  if (!text) return;
  const rules = getRules();
  rules.push({{ text, createdAt: new Date().toISOString() }});
  saveRules(rules);
  input.value = '';
  renderRules();
}}
function deleteRule(idx) {{
  const rules = getRules();
  rules.splice(idx, 1);
  saveRules(rules);
  renderRules();
}}
function renderRules() {{
  const rules = getRules();
  const el = document.getElementById('rules-list');
  if (!el) return;
  if (rules.length === 0) {{
    el.innerHTML = '<p style="color:var(--text-muted);font-size:.85rem">No rules yet. Run experiments and add what you learn.</p>';
    return;
  }}
  el.innerHTML = rules.map((r, i) => '<div style="display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--surface);border:1px solid var(--border);border-radius:8px;border-left:3px solid var(--green)">'
    + '<span style="font-size:.85rem;flex:1">' + _esc(r.text) + '</span>'
    + '<span style="font-size:.72rem;color:var(--text-muted)">' + r.createdAt.slice(0,10) + '</span>'
    + '<button onclick="deleteRule(' + i + ')" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:.85rem">✕</button></div>').join('');
}}
window.addRule = addRule;
window.deleteRule = deleteRule;

// ── AI Thumbnail Maker ────────────────────────────────────────
const HOST_PHOTOS_LS = 'okst_host_photos';
const OPENAI_KEY_LS = 'okst_openai_key';
let _thumbConversation = [];

function saveOpenAIKey() {{
  const v = document.getElementById('openai-key-input').value.trim();
  if (!v) return;
  localStorage.setItem(OPENAI_KEY_LS, v);
  document.getElementById('openai-key-input').value = '';
  document.getElementById('openai-key-bar').style.display = 'none';
  document.getElementById('openai-key-saved').style.display = 'flex';
}}
function clearOpenAIKey() {{
  localStorage.removeItem(OPENAI_KEY_LS);
  document.getElementById('openai-key-bar').style.display = 'flex';
  document.getElementById('openai-key-saved').style.display = 'none';
}}
window.saveOpenAIKey = saveOpenAIKey;
window.clearOpenAIKey = clearOpenAIKey;

function getHostPhotos() {{
  try {{ return JSON.parse(localStorage.getItem(HOST_PHOTOS_LS) || '[]'); }} catch {{ return []; }}
}}

function renderHostPhotos() {{
  const photos = getHostPhotos();
  const grid = document.getElementById('host-photos-grid');
  const empty = document.getElementById('host-photos-empty');
  if (!photos.length) {{ if (empty) empty.style.display = 'block'; return; }}
  if (empty) empty.style.display = 'none';
  // Remove old photo elements (keep empty placeholder)
  grid.querySelectorAll('.host-photo-item').forEach(el => el.remove());
  photos.forEach((p, i) => {{
    const div = document.createElement('div');
    div.className = 'host-photo-item';
    div.style.cssText = 'position:relative;border-radius:8px;overflow:hidden;border:1px solid var(--border);background:var(--surface)';
    div.innerHTML = '<img src="' + p.data + '" style="width:100%;aspect-ratio:1;object-fit:cover;display:block">'
      + '<div style="padding:4px 6px;font-size:.72rem;font-weight:600;text-align:center;background:var(--surface2)">' + _esc(p.name) + '</div>'
      + '<button onclick="removeHostPhoto(' + i + ')" style="position:absolute;top:4px;right:4px;background:rgba(0,0,0,.6);color:#fff;border:none;border-radius:50%;width:20px;height:20px;font-size:.7rem;cursor:pointer;display:flex;align-items:center;justify-content:center">x</button>';
    grid.appendChild(div);
  }});
}}

function handleHostPhotoUpload(files) {{
  const photos = getHostPhotos();
  Array.from(files).forEach(file => {{
    if (!file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = (e) => {{
      const name = prompt('Who is this? (e.g. Sam, John, Guest)', file.name.replace(/\\.[^.]+$/, ''));
      if (!name) return;
      photos.push({{ name: name, data: e.target.result, filename: file.name }});
      localStorage.setItem(HOST_PHOTOS_LS, JSON.stringify(photos));
      renderHostPhotos();
    }};
    reader.readAsDataURL(file);
  }});
}}
window.handleHostPhotoUpload = handleHostPhotoUpload;

function removeHostPhoto(idx) {{
  const photos = getHostPhotos();
  photos.splice(idx, 1);
  localStorage.setItem(HOST_PHOTOS_LS, JSON.stringify(photos));
  renderHostPhotos();
}}
window.removeHostPhoto = removeHostPhoto;

function _thumbChatBubble(role, html) {{
  const box = document.getElementById('thumb-chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-bubble ' + role;
  div.style.fontSize = '.82rem';
  div.innerHTML = html;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}}

async function _callClaude(messages, maxTokens, systemPrompt) {{
  const key = getKey();
  if (!key) {{ alert('Save your Anthropic API key in the Analytics tab first.'); return null; }}
  const body = {{ model: 'claude-sonnet-4-6', max_tokens: maxTokens || 1500, messages: messages }};
  if (systemPrompt) body.system = systemPrompt;
  const resp = await fetch('https://api.anthropic.com/v1/messages', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true' }},
    body: JSON.stringify(body)
  }});
  return await resp.json();
}}

async function _generateDalle(prompt) {{
  const key = localStorage.getItem(OPENAI_KEY_LS);
  if (!key) return null;
  try {{
    const resp = await fetch('https://api.openai.com/v1/images/generations', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key }},
      body: JSON.stringify({{ model: 'dall-e-3', prompt: prompt, n: 1, size: '1792x1024', quality: 'standard', response_format: 'b64_json' }})
    }});
    const data = await resp.json();
    if (data.data && data.data[0]) return 'data:image/png;base64,' + data.data[0].b64_json;
    return null;
  }} catch(e) {{ console.error('DALL-E error:', e); return null; }}
}}

async function generateThumbnails() {{
  const story = document.getElementById('thumb-story-input').value.trim();
  if (!story) {{ alert('Paste a story summary first.'); return; }}
  if (!getKey()) {{ alert('Save your Anthropic API key in the Analytics tab first.'); return; }}

  const btn = document.getElementById('thumb-gen-btn');
  const status = document.getElementById('thumb-gen-status');
  btn.disabled = true;
  status.textContent = 'Step 1/3: Generating concepts...';
  document.getElementById('thumb-results').style.display = 'block';

  const host = document.getElementById('thumb-host').value;
  const length = document.getElementById('thumb-length').value;
  const textStyle = document.getElementById('thumb-text-style').value;
  const hostPhotos = getHostPhotos();
  const hasPhotos = hostPhotos.length > 0;
  const hasDalle = !!localStorage.getItem(OPENAI_KEY_LS);

  // Get top competitor thumbnails for reference
  const topComp = COMP_VIDEOS.slice(0, 10).map(c => c.channel + ': "' + c.title + '" (' + (c.view_count||0).toLocaleString() + ' views)').join('\\n');

  // Build top performing own videos
  const topOwn = ALL_VIDEOS.filter(v => (v.dur||0) >= 5)
    .sort((a,b) => (b.v||0)-(a.v||0)).slice(0,10)
    .map(v => '"' + v.title + '" (' + (v.v||0).toLocaleString() + ' views)').join('\\n');

  const systemPrompt = 'You are a YouTube thumbnail + title expert for OKStorytime, a Reddit story reaction channel with 1.3M subscribers.\\n\\n'
    + 'CHANNEL THUMBNAIL RULES:\\n'
    + '- Host: extreme close-up (face fills 80%+ of frame), shocked/dramatic expression, direct eye contact\\n'
    + '- Background: purple/blue studio tones work best. Dark gradients also strong.\\n'
    + '- No guests in thumbnails, no red backgrounds, no cluttered layouts\\n'
    + '- Text overlay: ' + (textStyle === 'bold-keyword' ? '1-2 BIG BOLD words (e.g. "EXPOSED", "CAUGHT")' : textStyle === 'short-quote' ? 'Short dramatic quote from the story' : 'No text overlay, expression tells the story') + '\\n'
    + '- Host: ' + (host === 'sam' ? 'Sam solo' : host === 'john' ? 'John solo' : host === 'both' ? 'Sam + John' : 'Guest') + '\\n\\n'
    + 'TITLE RULES:\\n'
    + '- First-person drama ("My husband BONED his co-worker") massively outperforms third-person\\n'
    + '- Must create unresolved tension\\n'
    + '- 60-80 characters ideal\\n'
    + '- Strong emotional words: TRUTH, SECRET, REVEALED, ABANDONED, DEMANDED, EXPOSED\\n\\n'
    + 'TOP 10 OWN VIDEOS:\\n' + topOwn + '\\n\\n'
    + 'TOP 10 COMPETITOR VIDEOS:\\n' + topComp + '\\n\\n'
    + 'You MUST respond in EXACTLY this JSON format (no markdown, no code blocks, just raw JSON):\\n'
    + '[\\n'
    + '  {{\\n'
    + '    "style": "proven",\\n'
    + '    "title": "the title",\\n'
    + '    "thumbnail_description": "detailed visual description for image generation: host expression, background, colors, text overlay, composition",\\n'
    + '    "dalle_prompt": "professional YouTube thumbnail, 16:9 aspect ratio, [detailed scene description for DALL-E]. Do NOT include any real person. Show only the background/scene/mood.",\\n'
    + '    "why": "1 sentence explaining why this works based on the data"\\n'
    + '  }},\\n'
    + '  {{ "style": "competitor", ... }},\\n'
    + '  {{ "style": "experimental", ... }}\\n'
    + ']\\n\\n'
    + 'CONCEPT 1 (proven): Use OKStorytime\\'s exact proven formula. Purple/blue studio, extreme close-up, first-person shock title.\\n'
    + 'CONCEPT 2 (competitor): Inspired by what\\'s working for competitors right now. Different color palette or composition.\\n'
    + 'CONCEPT 3 (experimental): Something fresh and different that could stand out in the feed. Bold creative risk.';

  try {{
    const data = await _callClaude([{{ role: 'user', content: 'Story: ' + story }}], 2000, systemPrompt);
    if (!data || data.error) {{
      status.textContent = 'Error: ' + (data?.error?.message || 'API error');
      btn.disabled = false;
      return;
    }}

    let text = data.content?.[0]?.text || '';
    // Extract JSON from response (handle markdown code blocks)
    const jsonMatch = text.match(/\\[\\s*\\{{[\\s\\S]*\\}}\\s*\\]/);
    let concepts;
    try {{
      concepts = JSON.parse(jsonMatch ? jsonMatch[0] : text);
    }} catch(e) {{
      // Fallback: show raw text
      for (let i = 1; i <= 3; i++) {{
        document.getElementById('thumb-title-' + i).innerHTML = '';
        document.getElementById('thumb-why-' + i).innerHTML = '';
      }}
      document.getElementById('thumb-title-1').innerHTML = text.replace(/\\n/g, '<br>');
      status.textContent = 'Generated concepts (JSON parse failed, showing raw)';
      btn.disabled = false;
      return;
    }}

    // Store conversation for refinement
    _thumbConversation = [
      {{ role: 'user', content: 'Story: ' + story }},
      {{ role: 'assistant', content: text }}
    ];

    // Display concepts
    concepts.forEach((c, i) => {{
      const idx = i + 1;
      document.getElementById('thumb-title-' + idx).innerHTML = '<span style="color:var(--primary);font-weight:800">TITLE:</span> ' + _esc(c.title);
      document.getElementById('thumb-why-' + idx).innerHTML = '<strong>Why:</strong> ' + _esc(c.why) + '<br><strong>Thumbnail:</strong> ' + _esc(c.thumbnail_description);
    }});

    // Generate images if DALL-E key available
    if (hasDalle) {{
      status.textContent = 'Step 2/3: Generating thumbnail images with DALL-E...';
      const imgPromises = concepts.map(async (c, i) => {{
        const idx = i + 1;
        document.getElementById('thumb-img-' + idx).innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:.78rem"><div class="thinking-dot" style="margin-right:4px"></div><div class="thinking-dot" style="animation-delay:.3s;margin-right:4px"></div><div class="thinking-dot" style="animation-delay:.6s"></div></div>';
        try {{
          const imgData = await _generateDalle(c.dalle_prompt);
          if (imgData) {{
            document.getElementById('thumb-img-' + idx).innerHTML = '<img src="' + imgData + '" style="width:100%;height:100%;object-fit:cover;border-radius:8px">';
          }} else {{
            document.getElementById('thumb-img-' + idx).innerHTML = '<div style="padding:12px;font-size:.78rem;color:var(--text-muted);text-align:center">' + _esc(c.thumbnail_description) + '</div>';
          }}
        }} catch(e) {{
          document.getElementById('thumb-img-' + idx).innerHTML = '<div style="padding:12px;font-size:.78rem;color:var(--red)">Image generation failed</div>';
        }}
      }});
      await Promise.all(imgPromises);
      status.textContent = 'Done! Pick a concept and refine it below.';
    }} else {{
      // No DALL-E: show text descriptions in image slots
      concepts.forEach((c, i) => {{
        const idx = i + 1;
        document.getElementById('thumb-img-' + idx).innerHTML = '<div style="padding:12px;font-size:.78rem;color:var(--text-muted);text-align:center;line-height:1.4"><strong>Visual concept:</strong><br>' + _esc(c.thumbnail_description) + '</div>';
      }});
      status.textContent = 'Done! Add an OpenAI key below to generate actual thumbnail images.';
    }}
  }} catch(e) {{
    status.textContent = 'Error: ' + e.message;
  }}
  btn.disabled = false;
}}
window.generateThumbnails = generateThumbnails;

async function refineThumb() {{
  const input = document.getElementById('thumb-chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  if (!getKey()) {{ alert('Save your Anthropic API key in the Analytics tab first.'); return; }}

  input.value = '';
  _thumbChatBubble('user', _esc(msg));
  const thinking = _thumbChatBubble('assistant', '<em style="color:var(--text-muted)">Refining...</em>');

  _thumbConversation.push({{ role: 'user', content: msg + '\\n\\nRespond with the updated JSON array in the same format (3 concepts). Only modify what I asked to change.' }});

  try {{
    const data = await _callClaude(_thumbConversation, 2000);
    if (!data || data.error) {{
      thinking.innerHTML = '<span style="color:var(--red)">Error: ' + _esc(data?.error?.message || 'API error') + '</span>';
      return;
    }}

    const text = data.content?.[0]?.text || '';
    _thumbConversation.push({{ role: 'assistant', content: text }});

    const jsonMatch = text.match(/\\[\\s*\\{{[\\s\\S]*\\}}\\s*\\]/);
    if (jsonMatch) {{
      try {{
        const concepts = JSON.parse(jsonMatch[0]);
        concepts.forEach((c, i) => {{
          const idx = i + 1;
          document.getElementById('thumb-title-' + idx).innerHTML = '<span style="color:var(--primary);font-weight:800">TITLE:</span> ' + _esc(c.title);
          document.getElementById('thumb-why-' + idx).innerHTML = '<strong>Why:</strong> ' + _esc(c.why) + '<br><strong>Thumbnail:</strong> ' + _esc(c.thumbnail_description);
        }});
        thinking.innerHTML = 'Updated all 3 concepts! Check above.';

        // Re-generate images if DALL-E available
        if (localStorage.getItem(OPENAI_KEY_LS)) {{
          thinking.innerHTML += ' Regenerating images...';
          const imgPromises = concepts.map(async (c, i) => {{
            const idx = i + 1;
            document.getElementById('thumb-img-' + idx).innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:.78rem"><div class="thinking-dot" style="margin-right:4px"></div><div class="thinking-dot" style="animation-delay:.3s;margin-right:4px"></div><div class="thinking-dot" style="animation-delay:.6s"></div></div>';
            try {{
              const imgData = await _generateDalle(c.dalle_prompt);
              if (imgData) {{
                document.getElementById('thumb-img-' + idx).innerHTML = '<img src="' + imgData + '" style="width:100%;height:100%;object-fit:cover;border-radius:8px">';
              }}
            }} catch(e) {{}}
          }});
          await Promise.all(imgPromises);
          thinking.innerHTML = 'Updated concepts and regenerated all images!';
        }}
      }} catch(e) {{
        thinking.innerHTML = text.replace(/\\n/g, '<br>');
      }}
    }} else {{
      thinking.innerHTML = text.replace(/\\n/g, '<br>').replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
    }}
  }} catch(e) {{
    thinking.innerHTML = '<span style="color:var(--red)">Error: ' + _esc(e.message) + '</span>';
  }}
}}
window.refineThumb = refineThumb;

// ── Init experiments + rules + patterns + thumbnail maker on load ──
window.addEventListener('DOMContentLoaded', () => {{
  renderExperiments();
  renderRules();
  analyzeTitlePatterns();
  filterCompTitles('7d', null);
  renderHostPhotos();
  // Show OpenAI key saved indicator
  if (localStorage.getItem(OPENAI_KEY_LS)) {{
    document.getElementById('openai-key-bar').style.display = 'none';
    document.getElementById('openai-key-saved').style.display = 'flex';
  }}
}});

</script>
</body>
</html>"""


if __name__ == "__main__":
    print("Loading data...")
    videos = load_csv()
    print(f"Loaded {len(videos)} videos. Building report...")
    html = build(videos)
    # Output as index.html for GitHub Pages hosting
    # Also keep the named copy for local sharing
    for out in ["index.html", "OKStorytime_Analytics_Report.html"]:
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
    print(f"\nDone. Opens: index.html  /  OKStorytime_Analytics_Report.html")
