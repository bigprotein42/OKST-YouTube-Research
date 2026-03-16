HOW TO EXPORT FROM YOUTUBE STUDIO
==================================

Ask Sam to do these steps:

1. Go to https://studio.youtube.com
2. Click "Analytics" in the left sidebar
3. Click "Advanced Mode" (top right)
4. For each export below, set the date range (ideally "Lifetime" or last 2 years)

EXPORT 1 — Video Performance
  Tab: "Video"
  Columns visible: Video title, Views, Watch time (hours),
                   Impressions, Impressions CTR, Avg view duration,
                   Avg percentage viewed
  Click the download arrow (top right) → Export as CSV
  Save as: video_performance.csv

EXPORT 2 — Traffic Sources (optional)
  Tab: "Traffic source"
  Click download → Export as CSV
  Save as: traffic_sources.csv

EXPORT 3 — Revenue (optional, if monetized)
  Tab: "Revenue"
  Click download → Export as CSV
  Save as: revenue.csv

Drop all CSV files into this folder (studio_exports/).
Then run:  python ingest_studio_exports.py

The dashboard will automatically pick up the data on next report generation.
