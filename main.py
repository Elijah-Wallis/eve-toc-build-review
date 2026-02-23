import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            payload = json.dumps({"status": "ok", "service": "eve_autonomous_node"})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")

    def log_message(self, format, *args):  # pragma: no cover - reduce noise
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("Eve Protocol Active.")
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
