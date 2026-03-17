"""Local server for the YouTube Research dashboard.
Serves files + a /api/hosts endpoint that dynamically scans the host photos folder.
"""
import http.server, webbrowser, threading, os, json
from pathlib import Path

PORT = 8080
BASE_DIR = Path(__file__).parent
os.chdir(BASE_DIR)

HOST_PHOTOS_DIR = BASE_DIR / 'thumbnails' / '00_Host Pictures'
SKIP = {'.mp4', '.txt', '.py', '.json'}


def scan_hosts():
    """Scan host folders and return dict of host -> list of cutout filenames."""
    result = {}
    if not HOST_PHOTOS_DIR.exists():
        return result
    for host_dir in sorted(HOST_PHOTOS_DIR.iterdir()):
        if not host_dir.is_dir() or host_dir.name.startswith('.'):
            continue
        cutouts_dir = host_dir / 'cutouts'
        if not cutouts_dir.exists():
            continue
        files = sorted([
            f.name for f in cutouts_dir.iterdir()
            if f.is_file() and f.suffix.lower() not in SKIP
        ])
        if files:
            result[host_dir.name.lower()] = {
                'folder': host_dir.name,
                'files': files
            }
    return result


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass  # silence request logs

    def do_GET(self):
        if self.path == '/api/hosts':
            data = json.dumps(scan_hosts()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(data))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        else:
            super().do_GET()


def open_browser():
    webbrowser.open(f'http://localhost:{PORT}')


print(f"Dashboard running at http://localhost:{PORT}")
print("Press Ctrl+C to stop.\n")
threading.Timer(0.5, open_browser).start()
http.server.HTTPServer(('', PORT), Handler).serve_forever()
