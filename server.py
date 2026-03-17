"""Simple local server for the YouTube Research dashboard."""
import http.server, webbrowser, threading, os

PORT = 8080
os.chdir(os.path.dirname(os.path.abspath(__file__)))

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass  # silence request logs

def open_browser():
    webbrowser.open(f'http://localhost:{PORT}')

print(f"Dashboard running at http://localhost:{PORT}")
print("Press Ctrl+C to stop.\n")
threading.Timer(0.5, open_browser).start()
http.server.HTTPServer(('', PORT), Handler).serve_forever()
