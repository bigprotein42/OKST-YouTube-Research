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
    {"name": "Two Hot Takes",     "id": "UCvUW0xT38Ho7qyUmBgBZXQA"},
    {"name": "rSlash",            "id": "UC0-swBG9Ne0Vh4OuoJ2bjbA"},
    {"name": "MrBallen",          "id": "UCtPrkXdtCM5DACLufB9jbsA"},
    {"name": "Comfort Level",     "id": "UCJ8l9Mu5FOSQ1WFFhS4mlDA"},
    {"name": "Charlotte Dobre",   "id": "UCwc_RHwAPPaEh-jtwClpVrg"},
    {"name": "Am I the Jerk",     "id": "UCZKLuU6t7CaB_RD-bD4qdWw"},
    {"name": "Mark Narrations",   "id": "UCcmyNcmduQbuDrHxpL_3ojw"},
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
            videos.append(row)
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

    # Day of week (all time default)
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    day_data = {}
    for day in days:
        dv = [v for v in videos if v["publish_day_of_week"] == day]
        if dv:
            day_data[day] = {"avg": sum(v["view_count"] for v in dv)/len(dv), "count": len(dv)}
    max_day = max(d["avg"] for d in day_data.values())

    # Length buckets
    buckets = [
        ("Shorts (<2 min)", 0, 2), ("2–10 min", 2, 10), ("10–20 min", 10, 20),
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
    longform = [v for v in videos if v["duration_minutes"] >= 2]
    for yr in years:
        yv = [v for v in videos if v["publish_year"] == yr]
        year_data[yr] = {"avg": sum(v["view_count"] for v in yv)/len(yv), "count": len(yv)}
        sv = [v for v in shorts   if v["publish_year"] == yr]
        lv = [v for v in longform if v["publish_year"] == yr]
        if sv: shorts_yr[yr] = sum(v["view_count"] for v in sv)/len(sv)
        if lv: long_yr[yr]   = sum(v["view_count"] for v in lv)/len(lv)

    # Monthly data — ALL years for chart
    all_monthly = {}
    for v in videos:
        if v["publish_year"] >= 2022:
            k = f"{v['publish_year']}-{v['publish_month']:02d}"
            all_monthly.setdefault(k, []).append(v["view_count"])
    monthly_chart_data = [
        {"month": k, "avg": round(sum(vals)/len(vals)), "count": len(vals)}
        for k, vals in sorted(all_monthly.items())
    ]
    monthly_json = json.dumps(monthly_chart_data)

    # Monthly for legacy table (2023+)
    monthly = {}
    for v in videos:
        if v["publish_year"] >= 2023:
            k = f"{v['publish_year']}-{v['publish_month']:02d}"
            monthly.setdefault(k, []).append(v["view_count"])
    monthly_avgs = {k: sum(v)/len(v) for k, v in monthly.items()}
    max_mo = max(monthly_avgs.values())

    # Keywords
    top_q  = by_views[:len(videos)//4]
    bot_q  = by_views[-(len(videos)//4):]
    tw, bw = word_freq(top_q), word_freq(bot_q)
    top_kw = sorted([(w, c/max(bw.get(w,.5),.5)) for w,c in tw.items() if c>=5], key=lambda x:-x[1])[:10]
    bot_kw = sorted([(w, c/max(tw.get(w,.5),.5)) for w,c in bw.items() if c>=5], key=lambda x:-x[1])[:10]

    # Recent low performers
    recent = sorted([v for v in videos if v["publish_year"] >= 2024], key=lambda x: x["view_count"])

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
    for vid_id in thumb_pool_ids:
        path = f"thumbnails/top/{vid_id}.jpg"
        if os.path.exists(path):
            continue
        for q in ["hqdefault", "mqdefault"]:
            try:
                r = _req.get(f"https://i.ytimg.com/vi/{vid_id}/{q}.jpg", timeout=8)
                if r.status_code == 200 and len(r.content) > 3000:
                    with open(path, "wb") as f:
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
            b64 = img_b64(f"thumbnails/{folder}/{vid_id}.jpg")
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
            b64 = img_b64(f"thumbnails/top/{vid_id}.jpg") or img_b64(f"thumbnails/recent/{vid_id}.jpg")
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
    <div class="hstat"><div class="val">Sunday</div><div class="lbl">Best Post Day</div></div>
    <div class="hstat"><div class="val">60–90 min</div><div class="lbl">Best Format</div></div>
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
</nav>


<!-- ════════ SUMMARY ════════ -->
<div id="tab-summary" class="tab active">
  <div class="card" style="margin-top:22px">
    <div class="card-title">🔑 Why You're Losing Viewers — The Short Version</div>
    <div class="two-col" style="margin-top:14px;gap:14px">
      <div>
        <div style="font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--red);margin-bottom:8px">⚠ Problems</div>
        <div class="insight red">❌ <strong>Your Shorts collapsed 92%.</strong> They averaged 60,453 views in 2023. Now they average 4,784 in 2025. Yet you made 547 Shorts in 2025 — your highest Short volume ever.</div>
        <div class="insight red">❌ <strong>Volume is killing quality reach.</strong> October 2025: 184 videos uploaded → 8,640 avg views. April 2024: 50 videos → 36,276 avg views. Fewer videos = more views per video.</div>
        <div class="insight red">❌ <strong>New red studio isn't recognized.</strong> Viewers built a strong association with purple/blue + orange. The red backdrop looks like a different channel.</div>
        <div class="insight red">❌ <strong>Guest thumbnails don't convert.</strong> New viewers don't know your guests. Every guest thumbnail tested below average.</div>
      </div>
      <div>
        <div style="font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--green);margin-bottom:8px">✓ Wins</div>
        <div class="insight green">✅ <strong>Long-form is still working.</strong> Long-form averaged 39,629 views in 2024 — nearly 10× Shorts that same year.</div>
        <div class="insight green">✅ <strong>Sunday is your superpower.</strong> Sunday averages 46,649 views vs 15,035 on Wednesday — 3.1× difference purely from posting day.</div>
        <div class="insight green">✅ <strong>60–90 min compilations are your #1 format.</strong> 48,018 avg views — more than double Shorts, 10× better than 2–10 min clips.</div>
        <div class="insight green">✅ <strong>Your podcast is thriving.</strong> Apple Podcasts #5 Comedy in the US. That audience can convert to YouTube viewers.</div>
      </div>
    </div>
  </div>
  <div class="two-col">
    <div class="card">
      <div class="card-title">📉 The Decline Timeline</div>
      <div class="table-wrap"><table>
        <tr><th>Period</th><th>Avg Views</th><th>What Happened</th></tr>
        <tr><td>May 2023</td><td class="num green">155,619</td><td>Peak — viral Shorts era</td></tr>
        <tr><td>Jun 2023</td><td class="num" style="color:var(--yellow)">26,881</td><td>First cliff drop</td></tr>
        <tr><td>Apr 2024</td><td class="num green">36,276</td><td>Long-form recovery</td></tr>
        <tr><td>Oct 2024</td><td class="num red">13,596</td><td>Second drop</td></tr>
        <tr><td>Oct 2025</td><td class="num red">8,640</td><td>184 videos that month</td></tr>
        <tr><td>Nov 2025</td><td class="num red"><strong>4,126</strong></td><td>Lowest point ever</td></tr>
        <tr><td>Feb 2026</td><td class="num" style="color:var(--yellow)">8,177</td><td>Slight recovery</td></tr>
      </table></div>
    </div>
    <div class="card">
      <div class="card-title">⚡ Quick Stats</div>
      <div class="table-wrap"><table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Best single video ever</td><td class="num">7.5M views</td></tr>
        <tr><td>Best posting day</td><td class="num">Sunday (46,649 avg)</td></tr>
        <tr><td>Worst posting day</td><td class="num">Wednesday (15,035 avg)</td></tr>
        <tr><td>Best video length</td><td class="num">60–90 min (48,018 avg)</td></tr>
        <tr><td>Worst video length</td><td class="num">2–10 min (4,876 avg)</td></tr>
        <tr><td>Shorts peak (2023)</td><td class="num green">60,453 avg views</td></tr>
        <tr><td>Shorts now (2025)</td><td class="num red">4,784 avg views</td></tr>
        <tr><td>Long-form (2024)</td><td class="num green">39,629 avg views</td></tr>
        <tr><td>Est. monthly ad revenue</td><td class="num">$1,730–$5,180</td></tr>
      </table></div>
    </div>
  </div>
</div>


<!-- ════════ ACTION PLAN ════════ -->
<div id="tab-action" class="tab">
  <div class="card" style="margin-top:22px">
    <div class="card-title">🚀 5-Step Action Plan to Recover Views</div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 1 — Cut upload volume immediately</strong><br>Target: 3–5 videos/week max. April 2024 (50 videos/month) = 36,276 avg. October 2025 (184 videos/month) = 8,640 avg. Every extra video dilutes your best content's reach.</div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 2 — Make Sunday your flagship drop</strong><br>Sunday averages 46,649 views — 3.1× better than Wednesday. Best episode of the week goes live Sunday. Try a consistent time: 10am–12pm ET.</div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 3 — Rebuild around 60–90 min compilations</strong><br>Your highest-avg format (48,018 views). Use: <span class="tag g">MEGA</span><span class="tag g">weekly recap</span><span class="tag g">compilation</span><span class="tag g">truth</span><span class="tag g">reaction</span><br>Structure: 3–4 stories per episode, 15–20 min each, timestamps in description.</div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 4 — Fix the thumbnail formula</strong><br><span class="tag g">Sam or John only</span><span class="tag g">extreme close-up (face fills 80%+)</span><span class="tag g">consistent studio background</span><span class="tag g">mouth open, shocked expression</span><span class="tag g">direct eye contact with camera</span><br><span class="tag r">no guests</span><span class="tag r">no red background</span><span class="tag r">no profile/side shots</span><span class="tag r">no livestream screenshots</span></div>
    <div class="insight green" style="margin-bottom:12px"><strong>Step 5 — Lead titles with the story, not the host</strong><br><span class="tag g">Drama-first</span>: "My husband BONED his co-worker" → 1.9M views<br><span class="tag r">Host-first</span>: "Denise reacts to..." → consistently bottom 25%</div>
    <div class="insight yellow"><strong>⚡ Phase 2 — Get Sam's OAuth access for deeper data</strong><br>Unlocks: watch time per video, audience retention curves, CTR per thumbnail, revenue per video. Ask Sam to connect the channel owner Google account. This will give 10× more precise recommendations.</div>
  </div>
  <div class="card">
    <div class="card-title">📋 Weekly Content Schedule Template</div>
    <div class="table-wrap"><table>
      <tr><th>Day</th><th>Content</th><th>Format</th><th>Why</th></tr>
      <tr class="highlight-row"><td><strong>Sunday ⭐</strong></td><td>Weekly Mega Recap / Compilation</td><td>60–90 min</td><td>Best day (46,649 avg) + best format (48,018 avg)</td></tr>
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
          <span style="color:var(--yellow)">→ OKStorytime parallel:</span> The podcast is at Apple #5 Comedy. That audience doesn't automatically watch YouTube. A direct "come watch us on YouTube" push could be a quick win.
        </div>
      </div>
    </div>

    <div class="insight yellow" style="margin-top:4px">
      <strong>The universal pattern across every comeback:</strong> Cut volume → pick one flagship format → be consistent for 90 days → make the change public so your existing audience knows to re-engage.
      Based on OKStorytime's data, the target is <strong>Sunday 60–90 min compilations, 3 videos/week max, purple studio, Sam or John only</strong>. Give it 90 days of strict adherence before evaluating.
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
    <p style="font-size:.84rem;color:var(--text-muted);margin-bottom:12px">True long-form only (5+ min horizontal) · tap a period to filter.</p>
    <div id="thumb-filters" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
      <button class="filter-btn active" onclick="filterThumbs('all',this)">Lifetime</button>
      {thumb_year_btns}
      <button class="filter-btn" onclick="filterThumbs('last30',this)">Last 30 Days</button>
      <button class="filter-btn" onclick="filterThumbs('last7',this)">Last 7 Days</button>
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
  </div>

</div>


<!-- ════════ ANALYTICS ════════ -->
<div id="tab-analytics" class="tab">

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
      <div class="card-title">📅 Avg Views by Day of Week</div>
      <div class="table-wrap">
      <table id="day-table">
        <tr><th>Day</th><th>Avg Views</th><th>Videos</th><th></th></tr>
      </table>
      </div>
      <p id="day-note" style="font-size:.82rem;color:var(--text-muted);margin-top:10px"></p>
    </div>
    <div class="card">
      <div class="card-title">⏱️ Avg Views by Video Length</div>
      <div class="table-wrap">
      <table id="len-table">
        <tr><th>Length</th><th>Avg Views</th><th>Videos</th><th></th></tr>
      </table>
      </div>
      <p id="len-note" style="font-size:.82rem;color:var(--text-muted);margin-top:10px"></p>
    </div>
  </div>

  <!-- Monthly Trend Chart -->
  <div class="card">
    <div class="card-title">📆 Monthly Avg Views Trend</div>
    <div class="filter-bar" id="chart-filters">
      <span>Time range:</span>
      <button class="filter-btn" onclick="setChartRange(3,this)">3M</button>
      <button class="filter-btn" onclick="setChartRange(6,this)">6M</button>
      <button class="filter-btn active" onclick="setChartRange(12,this)">1Y</button>
      <button class="filter-btn" onclick="setChartRange(24,this)">2Y</button>
      <button class="filter-btn" onclick="setChartRange(0,this)">All Time</button>
    </div>
    <div class="chart-container">
      <canvas id="monthly-chart"></canvas>
    </div>
    <div class="chart-meta" id="chart-meta"></div>
    <p style="font-size:.78rem;color:var(--text-muted);margin-top:12px">
      💡 <strong>Daily/weekly granularity</strong> requires the YouTube Analytics API — ask Sam to connect his Google account for deeper data.
      &nbsp;·&nbsp; <strong>Auto-update:</strong> Re-run <code style="background:#f5f3ff;padding:1px 5px;border-radius:4px;color:var(--primary)">fetch_channel_data.py</code> then <code style="background:#f5f3ff;padding:1px 5px;border-radius:4px;color:var(--primary)">generate_report.py</code> to refresh this report.
    </p>
  </div>

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
        <span class="chat-chip" onclick="askChip(this)">How do Shorts compare to long-form?</span>
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
        <p style="font-size:.82rem;color:var(--text-muted);margin-top:10px">Use: <span class="tag g">mega</span><span class="tag g">weekly</span><span class="tag g">recap</span><span class="tag g">compilation</span><span class="tag g">truth</span><span class="tag g">dark</span><span class="tag g">reaction</span></p>
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
    <div class="card-title">🏆 Competitor Titles — Last 7 Days</div>
    <p style="font-size:.83rem;color:var(--text-muted);margin:0 0 12px">What Two Hot Takes, rSlash, MrBallen &amp; Comfort Level are titling this week. Study the patterns.</p>
    <div style="display:flex;flex-direction:column;gap:6px">
      {comp_title_rows()}
    </div>
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
    <!-- Livestream filter -->
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
      <span style="font-size:.82rem;color:var(--text-muted);font-weight:600">Filter:</span>
      <button class="live-filter-btn active" onclick="filterLiveRows(this,'all')" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--primary);color:#fff;font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">All</button>
      <button class="live-filter-btn" onclick="filterLiveRows(this,'hide')" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Hide Livestreams</button>
      <button class="live-filter-btn" onclick="filterLiveRows(this,'only')" style="padding:5px 14px;border:1px solid var(--border);border-radius:20px;background:var(--surface);color:var(--text-muted);font-size:.8rem;cursor:pointer;font-family:inherit;font-weight:600">Livestreams Only</button>
    </div>
    <div class="card">
      <div class="card-title">🏆 Top 20 Long-Form Videos (5+ min)</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:10px">Sorted by all-time views. These are your proven formats — click any title to watch.</p>
      <div class="table-wrap">{make_video_table(top_longform)}</div>
    </div>
    <div class="card">
      <div class="card-title">⚠️ Lowest Performing Long-Form (2024+)</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:10px">Long-form videos from 2024 onward with fewest views. Study what went wrong.</p>
      <div class="table-wrap">{make_video_table(bot_longform, "red")}</div>
    </div>
  </div>

  <!-- Shorts sub-tab -->
  <div id="video-sub-shorts" style="display:none">
    <div class="card">
      <div class="card-title">⚡ Top 20 Shorts of All Time</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:10px">Every top Short is from 2022–2023. The algorithm that made these work no longer exists.</p>
      <div class="table-wrap">{make_video_table(top_shorts_vd)}</div>
    </div>
    <div class="card">
      <div class="card-title">⚠️ Lowest Performing Shorts (2024+)</div>
      <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:10px">Recent Shorts that aren't working. Mostly livestream clips, subreddit-tagged, or random fragments.</p>
      <div class="table-wrap">{make_video_table(bot_shorts_vd, "red")}</div>
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
      <tr class="highlight-row"><td><strong>OKStorytime (you)</strong></td><td><strong>182K</strong></td><td><strong>~22K</strong></td><td><strong>4-host live show</strong></td><td><strong>Apple Podcasts #5 Comedy, iHeart distributed</strong></td></tr>
      <tr><td>Comfort Level</td><td>176K YT</td><td>Unknown</td><td>Multi-host podcast</td><td>TikTok-first (812K followers)</td></tr>
      <tr><td>PRIVATE DIARY</td><td>750K</td><td>~30K</td><td>TTS/animated narration</td><td>Consistent aesthetic, faceless format</td></tr>
      <tr><td>Las Damitas Histeria 🇲🇽</td><td>355K YT / 13.4M TikTok</td><td>70K–150K (episodes)</td><td>2-host comedy podcast + clip machine</td><td>Franchise model: book, live touring, Patreon + Sonoro network</td></tr>
    </table></div>
  </div>

  <div class="card">
    <div class="card-title">🌎 Las Damitas Histeria — Spanish-Language Competitor Breakdown</div>
    <p style="font-size:.82rem;color:var(--text-muted);margin-bottom:14px">2-host Mexican comedy podcast under Sonoro network. Founded Feb 2023. 355K YouTube subs, 50M+ views, 13.4M TikTok followers, live touring shows across Latin America and Europe. This is what a fully scaled version of OKStorytime looks like — in another language.</p>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
      <div>
        <div style="font-size:.78rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">What They Do Differently</div>
        <div class="insight green" style="margin-bottom:8px"><strong>Verdict format, not storytime.</strong> They don't just read stories — they render a verdict. Every episode has a clear "who's right / who's wrong" outcome the audience votes on. Viewers aren't passive — they're jury members.</div>
        <div class="insight green" style="margin-bottom:8px"><strong>Proprietary vocabulary builds a tribe.</strong> "Damita" (their fan name), "Ramiro" (their word for any bad boyfriend), "histeriquilla/eneje" (verdict labels) — their audience adopts this language. It turns viewers into members of a club. None of your competitors have this.</div>
        <div class="insight green" style="margin-bottom:8px"><strong>Clip machine strategy.</strong> One weekly 60-min recording session produces: 1 full episode + 4–6 standalone clips, all published across the same week. They hit 7–10 uploads/week without extra recording time.</div>
        <div class="insight green"><strong>Audio + video simultaneously.</strong> Because it's two hosts talking at a table, the episodes work perfectly as podcasts on Spotify AND as YouTube videos. English storytime channels are almost always video-first. This doubles their distribution.</div>
      </div>
      <div>
        <div style="font-size:.78rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">What You Can Steal</div>
        <div class="insight yellow" style="margin-bottom:8px"><strong>Give your audience a verdict name.</strong> "OKStorytime Jury," "The Council," "The Verdict Squad" — something that makes fans feel like participants, not viewers. Use it consistently in titles, comments, and on-air.</div>
        <div class="insight yellow" style="margin-bottom:8px"><strong>Name the villain archetype.</strong> "Ramiro" is genius — it's a named stand-in for every bad partner in every story. You could create your own recurring nickname for the antagonist in your stories. It creates inside-joke culture that keeps people coming back.</div>
        <div class="insight yellow" style="margin-bottom:8px"><strong>The clip machine model.</strong> You're already recording 1–3 hour live shows. You should be extracting 4–6 standalone clip videos per episode — the most shocking moment, the funniest reaction, the best verdict, the biggest disagreement. Post them as separate videos through the week.</div>
        <div class="insight yellow"><strong>Their full episodes hit 70K–150K regularly.</strong> They have fewer subscribers than you. The difference is intentional format structure: numbered episodes (T3 E47), clear topic labels, consistent release cadence. Viewers know exactly what they're getting.</div>
      </div>
    </div>

    <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px;margin-top:4px">
      <div style="font-size:.78rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Their Engagement Numbers (Recent Episodes)</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px 14px;font-size:.82rem"><span style="font-weight:700;color:var(--primary)">150,920</span> views — T3 E47 Fracasos en la cocina</div>
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px 14px;font-size:.82rem"><span style="font-weight:700;color:var(--primary)">138,771</span> views — viral clip from episode</div>
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px 14px;font-size:.82rem"><span style="font-weight:700;color:var(--primary)">73,467</span> views — T3 E48 Opiniones impopulares</div>
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px 14px;font-size:.82rem"><span style="font-weight:700;color:var(--primary)">70,546</span> views — T2 E1 Momentos (guest episode)</div>
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px 14px;font-size:.82rem"><span style="font-weight:700;color:var(--primary)">4.2%</span> rating rate — unusually high, signals deep loyalty</div>
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px 14px;font-size:.82rem"><span style="font-weight:700;color:var(--primary)">209K</span> views — all-time top video</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">💡 What Top Competitors Do That You Could Adopt</div>
    <div class="insight green"><strong>MrBallen's Easter Egg System:</strong> He hides something in every video and pins the first comment that finds it — forces full watch-throughs. You could hide a callback joke or "story of the week" answer that only makes sense if you watched the whole episode.</div>
    <div class="insight green"><strong>Two Hot Takes' TikTok Funnel:</strong> They clip the most shocking 30-second moment from every episode for TikTok. Their TikTok (812K) feeds YouTube. You have 1.1M TikTok followers — use them harder to drive YouTube watch time.</div>
    <div class="insight green"><strong>rSlash's Consistent Format:</strong> Every video follows the exact same structure. Viewers know exactly what they're getting. Your show format is strong — but thumbnail/title inconsistency confuses new visitors.</div>
    <div class="insight yellow"><strong>The CPM opportunity:</strong> Relationship/AITA drama earns $4–8 RPM. True crime adjacent content earns $6–12 RPM. You're in the right niche — you just need the views to capitalize on it.</div>
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
const COMP_THUMBS  = {comp_thumb_dict_json};

const DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
const BUCKETS = [
  ["Shorts (<2 min)", 0, 2], ["2-10 min", 2, 10], ["10-20 min", 10, 20],
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

  const vids = year === 'all' ? ALL_VIDEOS : ALL_VIDEOS.filter(v => v.y === parseInt(year));

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

// ── Monthly Chart (Chart.js) ────────────────────────────────────
let monthlyChart = null;

function setChartRange(months, btn) {{
  document.querySelectorAll('#chart-filters .filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const data = months === 0 ? MONTHLY_DATA : MONTHLY_DATA.slice(-months);
  drawChart(data);
}}

function drawChart(data) {{
  const labels = data.map(d => d.month);
  const values = data.map(d => d.avg);
  const counts = data.map(d => d.count);
  const peak   = Math.max(...values);
  const peakIdx = values.indexOf(peak);

  if (monthlyChart) monthlyChart.destroy();

  const ctx = document.getElementById('monthly-chart').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 300);
  grad.addColorStop(0, 'rgba(124,58,237,0.25)');
  grad.addColorStop(1, 'rgba(124,58,237,0)');

  monthlyChart = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels,
      datasets: [{{
        label: 'Avg Views',
        data: values,
        borderColor: '#7c3aed',
        backgroundColor: grad,
        borderWidth: 2.5,
        fill: true,
        tension: 0.4,
        pointRadius: data.length > 24 ? 2 : 4,
        pointHoverRadius: 7,
        pointBackgroundColor: values.map((v,i) => i===peakIdx ? '#f59e0b' : '#7c3aed'),
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
            label: ctx => ` ${{ctx.parsed.y.toLocaleString()}} avg views`,
            afterLabel: ctx => ` ${{counts[ctx.dataIndex]}} videos uploaded`,
          }}
        }}
      }},
      scales: {{
        x: {{
          grid: {{ display: false }},
          border: {{ display: false }},
          ticks: {{
            color: '#9ca3af',
            font: {{ size: 11 }},
            maxTicksLimit: 14,
            maxRotation: 45,
          }}
        }},
        y: {{
          grid: {{ color: 'rgba(0,0,0,0.04)', drawBorder: false }},
          border: {{ display: false }},
          ticks: {{
            color: '#9ca3af',
            font: {{ size: 11 }},
            callback: v => fmtK(v),
          }}
        }}
      }}
    }}
  }});

  // Update meta stats
  const avg = Math.round(values.reduce((a,b)=>a+b,0)/values.length);
  const recent3 = values.slice(-3);
  const avg3 = Math.round(recent3.reduce((a,b)=>a+b,0)/recent3.length);
  document.getElementById('chart-meta').innerHTML = `
    <div class="chart-stat"><span class="cs-val">${{fmtK(peak)}}</span><br><span class="cs-lbl">Peak in this period (${{labels[peakIdx]}})</span></div>
    <div class="chart-stat"><span class="cs-val">${{fmtK(avg)}}</span><br><span class="cs-lbl">Average over period</span></div>
    <div class="chart-stat"><span class="cs-val" style="color:${{avg3 > avg ? 'var(--green)' : 'var(--red)'}}">${{fmtK(avg3)}}</span><br><span class="cs-lbl">Last 3-month avg ${{avg3 > avg ? '▲ trending up' : '▼ trending down'}}</span></div>
  `;
}}

// ── Thumbnail filter ─────────────────────────────────────────────
function filterThumbs(period, btn) {{
  document.querySelectorAll('#thumb-filters .filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const now = new Date();
  const longform = ALL_VIDEOS.filter(v => v.duration_minutes >= 5);

  let pool;
  if (period === 'all') {{
    pool = [...longform].sort((a,b) => b.view_count - a.view_count);
  }} else if (period === 'last7') {{
    const cut = new Date(now - 7*86400000).toISOString().slice(0,10);
    pool = longform.filter(v => v.publish_date >= cut).sort((a,b) => b.view_count - a.view_count);
  }} else if (period === 'last30') {{
    const cut = new Date(now - 30*86400000).toISOString().slice(0,10);
    pool = longform.filter(v => v.publish_date >= cut).sort((a,b) => b.view_count - a.view_count);
  }} else {{
    const yr = parseInt(period);
    pool = longform.filter(v => v.publish_year === yr).sort((a,b) => b.view_count - a.view_count);
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
    return '<div class="thumb-item">'
      + '<a href="https://youtube.com/watch?v='+v.video_id+'" target="_blank">'
      + '<img src="data:image/jpeg;base64,'+THUMB_DICT[v.video_id]+'" alt="'+title+'" loading="lazy"></a>'
      + '<div class="thumb-label">'
      + '<strong>'+views+' views</strong>'
      + '<div style="display:flex;gap:6px;margin:3px 0 4px;align-items:center">'
      + '<span class="thumb-dur">'+Math.round(v.duration_minutes)+' min</span>'
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

// ── Video sub-tabs ───────────────────────────────────────────────
function filterLiveRows(btn, mode) {{
  document.querySelectorAll('.live-filter-btn').forEach(b => {{
    b.style.background = 'var(--surface)';
    b.style.color = 'var(--text-muted)';
    b.style.borderColor = 'var(--border)';
  }});
  btn.style.background = 'var(--primary)';
  btn.style.color = '#fff';
  btn.style.borderColor = 'var(--primary)';
  const container = document.getElementById('video-sub-longform');
  container.querySelectorAll('tbody tr').forEach(row => {{
    const isLive = row.dataset.live === '1';
    if (mode === 'all') row.style.display = '';
    else if (mode === 'hide') row.style.display = isLive ? 'none' : '';
    else if (mode === 'only') row.style.display = isLive ? '' : 'none';
  }});
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

// ── Competitor thumbnail filter ──────────────────────────────────
function filterCompThumbs(period, btn) {{
  document.querySelectorAll('#comp-thumb-filters .filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const now = new Date();
  let cutoff;
  if (period === 'week') cutoff = new Date(now - 7*24*60*60*1000);
  else if (period === 'month') cutoff = new Date(now - 30*24*60*60*1000);
  else if (period === 'year') cutoff = new Date(now - 365*24*60*60*1000);
  else cutoff = new Date(0);

  const pool = COMP_VIDEOS
    .filter(v => COMP_THUMBS[v.video_id] && new Date(v.published_date) >= cutoff)
    .sort((a,b) => b.view_count - a.view_count);

  const grid = document.getElementById('comp-thumb-grid');
  if (!grid) return;

  if (pool.length === 0) {{
    grid.innerHTML = '<p style="color:var(--text-muted);padding:12px 0">No competitor thumbnails for this period yet. History builds daily with each refresh.</p>';
    return;
  }}

  grid.innerHTML = '<div class="thumb-grid">' + pool.map(v => {{
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
}}

// ── Initialize ──────────────────────────────────────────────────
filterAnalytics('all', document.getElementById('fb-all'));
drawChart(MONTHLY_DATA.slice(-12));
filterThumbs('all', null);
filterCompThumbs('week', null);
filterTitles('all', null);

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
    const monthly = MONTHLY_DATA.map(m => m.month+': '+m.avg+' avg ('+m.count+' videos)').join(', ');
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

    const topWords = ['mega','weekly','recap','compilation','truth','dark','reaction','hours','proposed','abandoned'];
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
      + '- Best performing words to include if natural: mega, weekly, recap, compilation, truth, dark, reaction, hours\\n'
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
  window.sendChat = sendChat;
  window.askChip = askChip;
  window.generateTitles = generateTitles;
}})();
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
