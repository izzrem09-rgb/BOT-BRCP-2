from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write("Servidor funcionando ✅".encode("utf-8"))

server = HTTPServer(("0.0.0.0", 8080), Handler)
print("🌐 Servidor de prueba iniciado en http://0.0.0.0:8080")
server.serve_forever()
