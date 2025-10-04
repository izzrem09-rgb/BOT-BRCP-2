# keep_alive_simple.py (sin dependencias)
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write("Bot activo ✅".encode("utf-8"))

def run_server():
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()

def keep_alive():
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
