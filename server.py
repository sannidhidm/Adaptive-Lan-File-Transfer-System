from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
STORAGE_ROOT = ROOT / "transfers"
MAX_CHUNK_BYTES = 8 * 1024 * 1024
PROBE_LIMIT_BYTES = 2 * 1024 * 1024
CHUNK_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
STORAGE_LOCK = threading.Lock()


class TransferHandler(BaseHTTPRequestHandler):
    server_version = "AdaptiveLANTransfer/1.0"

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/":
            self._serve_static(WEB_ROOT / "index.html")
        elif request.path == "/api/files":
            self._send_json(self._list_files())
        elif request.path == "/api/probe":
            self._send_probe(parse_qs(request.query))
        elif request.path.startswith("/api/files/"):
            self._send_file(unquote(request.path.removeprefix("/api/files/")))
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        request = urlsplit(self.path)
        if request.path != "/api/upload":
            self._send_json({"error": "Not found"}, 404)
            return

        try:
            self._receive_chunk(parse_qs(request.query))
        except (ValueError, OSError) as error:
            self._send_json({"error": str(error)}, 400)

    def _receive_chunk(self, query: dict[str, list[str]]) -> None:
        upload_id = self._query_value(query, "id")
        name = self._safe_filename(self._query_value(query, "name"))
        part = self._parse_integer(self._query_value(query, "part"), "part", 0)
        final = self._query_value(query, "final")
        if not CHUNK_ID_PATTERN.fullmatch(upload_id):
            raise ValueError("Invalid upload identifier")
        if part > 1_000_000 or final not in {"true", "false"}:
            raise ValueError("Invalid chunk number")

        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
        except ValueError as error:
            raise ValueError("Invalid content length") from error
        if content_length < 0 or content_length > MAX_CHUNK_BYTES:
            raise ValueError("Chunk must be between 0 and 8 MiB")

        payload = self.rfile.read(content_length)
        if len(payload) != content_length:
            raise ValueError("Incomplete chunk received")

        STORAGE_ROOT.mkdir(exist_ok=True)
        part_dir = STORAGE_ROOT / upload_id
        part_dir.mkdir(exist_ok=True)
        (part_dir / f"{part:08d}.part").write_bytes(payload)

        if final == "true":
            with STORAGE_LOCK:
                chunk_paths = [part_dir / f"{index:08d}.part" for index in range(part + 1)]
                if any(not chunk_path.is_file() for chunk_path in chunk_paths):
                    raise ValueError("A chunk is missing; retry the upload")
                output_path = self._available_destination(name)
                with output_path.open("wb") as output:
                    for chunk_path in chunk_paths:
                        with chunk_path.open("rb") as chunk:
                            while block := chunk.read(1024 * 1024):
                                output.write(block)
                for chunk_path in part_dir.iterdir():
                    chunk_path.unlink()
                part_dir.rmdir()
            self._send_json({"received": part, "complete": True, "file": output_path.name})
            return

        self._send_json({"received": part, "complete": False})

    def _send_probe(self, query: dict[str, list[str]]) -> None:
        requested = query.get("bytes", ["524288"])[0]
        try:
            size = max(64 * 1024, min(int(requested), PROBE_LIMIT_BYTES))
        except ValueError:
            size = 512 * 1024

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        block = bytes(64 * 1024)
        try:
            remaining = size
            while remaining:
                piece = block[: min(remaining, len(block))]
                self.wfile.write(piece)
                remaining -= len(piece)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _list_files(self) -> list[dict[str, object]]:
        if not STORAGE_ROOT.exists():
            return []
        files = []
        for path in STORAGE_ROOT.iterdir():
            if path.is_file():
                stat = path.stat()
                files.append({"name": path.name, "size": stat.st_size, "modified": stat.st_mtime})
        return sorted(files, key=lambda item: float(item["modified"]), reverse=True)

    def _send_file(self, name: str) -> None:
        safe_name = self._safe_filename(name)
        path = STORAGE_ROOT / safe_name
        if not path.is_file():
            self._send_json({"error": "File not found"}, 404)
            return

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
        self.end_headers()
        try:
            with path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    self.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_static(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "Page not found"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, value: object, status: int = 200) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _query_value(query: dict[str, list[str]], key: str) -> str:
        values = query.get(key)
        if not values:
            raise ValueError(f"Missing {key}")
        return values[0]

    @staticmethod
    def _parse_integer(value: str, label: str, minimum: int) -> int:
        try:
            result = int(value)
        except ValueError as error:
            raise ValueError(f"Invalid {label}") from error
        if result < minimum:
            raise ValueError(f"Invalid {label}")
        return result

    @staticmethod
    def _safe_filename(name: str) -> str:
        name = name.replace("\\", "/").split("/")[-1]
        name = re.sub(r"[^A-Za-z0-9._ ()-]", "_", name).strip(" .")
        if not name:
            raise ValueError("Invalid filename")
        return name[:160]

    def _available_destination(self, name: str) -> Path:
        destination = STORAGE_ROOT / name
        if not destination.exists():
            return destination
        original = Path(name)
        for number in range(1, 10_000):
            candidate = STORAGE_ROOT / f"{original.stem} ({number}){original.suffix}"
            if not candidate.exists():
                return candidate
        raise ValueError("Too many files with this name")

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive LAN file transfer server")
    parser.add_argument("--host", default="0.0.0.0", help="Interface to listen on (default: all LAN interfaces)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    args = parser.parse_args()

    STORAGE_ROOT.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), TransferHandler)
    print(f"Adaptive LAN File Transfer is running at http://localhost:{args.port}")
    print("For other devices on your LAN, open http://<this-computer-LAN-IP>:" + str(args.port))
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
