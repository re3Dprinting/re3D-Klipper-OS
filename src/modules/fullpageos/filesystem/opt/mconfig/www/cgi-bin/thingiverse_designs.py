#!/usr/bin/python3
"""Store public design metadata collected by the local touchscreen browser."""

from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit


CACHE = Path("/var/lib/re3d-news/thingiverse.json")
AIRGAP = (Path("/etc/re3d-airgap/enabled"), Path("/run/re3d-airgap/enabled"))
LOCKDOWN = Path("/etc/re3d-security/lockdown")
MAX_BYTES = 16384


def image_url(value):
    if not isinstance(value, str) or not 0 < len(value) <= 2048:
        raise ValueError("Invalid design image.")
    url = urlsplit(value)
    if (url.scheme != "https" or url.hostname not in
            ("cdn.thingiverse.com", "resize.thingiverse.com")
            or url.username or url.password or url.port not in (None, 443)):
        raise ValueError("Design images must come from Thingiverse.")
    if url.hostname == "resize.thingiverse.com":
        source = parse_qs(url.query).get("url", [])
        if len(source) != 1 or urlsplit(source[0]).hostname != "cdn.thingiverse.com":
            raise ValueError("Invalid Thingiverse thumbnail source.")
        image_url(source[0])
    return value


def validate_designs(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 6:
        raise ValueError("Expected one to six designs.")
    designs = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            raise ValueError("Invalid design title.")
        title = " ".join(item["title"].split())
        if not 0 < len(title) <= 200:
            raise ValueError("Design titles must contain 1-200 characters.")
        designs.append({"title": title, "image": image_url(item.get("image"))})
    return designs


def read_cache():
    try:
        with CACHE.open(encoding="utf-8") as source:
            text = source.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return {"designs": [], "updatedAt": None, "checkedAt": None, "lastError": None}
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Saved design metadata is too large.")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Invalid saved design metadata.")
    designs = data.get("designs")
    if designs != []:
        validate_designs(designs)
    for key in ("updatedAt", "checkedAt"):
        value = data.get(key)
        if value is not None and (not isinstance(value, str)
                                  or datetime.fromisoformat(value).tzinfo is None):
            raise ValueError("Invalid saved design timestamp.")
    error = data.get("lastError")
    if error is not None and (not isinstance(error, str) or not 0 < len(error) <= 300):
        raise ValueError("Invalid saved collection error.")
    if designs and data.get("updatedAt") is None:
        raise ValueError("Saved designs have no update timestamp.")
    return data


def save_cache(data):
    CACHE.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=CACHE.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o644)
        temporary.replace(CACHE)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def respond(status, data):
    print(f"Status: {status}")
    print("Content-Type: application/json")
    print("Cache-Control: no-store")
    print()
    print(json.dumps(data))


def main():
    method = os.environ.get("REQUEST_METHOD", "GET")
    if method not in ("GET", "POST"):
        respond("405 Method Not Allowed", {"error": "Use GET or POST."})
        return
    try:
        if method == "POST":
            if not ipaddress.ip_address(os.environ.get("REMOTE_ADDR", "")).is_loopback:
                respond("403 Forbidden", {"error": "Only the touchscreen can update design metadata."})
                return
            if any(flag.exists() for flag in AIRGAP) or LOCKDOWN.exists():
                respond("403 Forbidden", {"error": "Design collection is disabled by printer security policy."})
                return
            if os.environ.get("CONTENT_TYPE") != "application/json":
                respond("415 Unsupported Media Type", {"error": "Expected application/json."})
                return
            length = int(os.environ.get("CONTENT_LENGTH", "0"))
            if not 0 < length <= MAX_BYTES:
                raise ValueError("Invalid design request length.")
            payload = json.loads(sys.stdin.read(length))
            if not isinstance(payload, dict) or len(payload) != 1:
                raise ValueError("Expected designs or a collection error.")
            if "designs" in payload:
                designs = validate_designs(payload["designs"])
                error = None
            elif isinstance(payload.get("error"), str) and 0 < len(payload["error"]) <= 300:
                designs = None
                error = payload["error"]
            else:
                raise ValueError("Expected designs or a collection error.")
    except (ValueError, UnicodeError) as error:
        print(f"Thingiverse request: {error}", file=sys.stderr)
        respond("400 Bad Request", {"error": str(error)})
        return
    try:
        data = read_cache()
        if method == "POST":
            data["checkedAt"] = datetime.now(timezone.utc).isoformat()
            data["lastError"] = error
            if designs is not None:
                data["designs"] = designs
                data["updatedAt"] = data["checkedAt"]
            save_cache(data)
        respond("200 OK", data)
    except (OSError, ValueError, UnicodeError) as error:
        print(f"Thingiverse cache: {error}", file=sys.stderr)
        respond("500 Internal Server Error", {"error": "Unable to read or save the printer's design cache."})


if __name__ == "__main__":
    main()
