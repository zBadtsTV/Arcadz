"""Compare Lavalink search/load latency. Requires only Python 3."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

query = " ".join(sys.argv[1:]).strip() or "scsearch:lofi hip hop"
if not query.startswith(("scsearch:", "http://", "https://")):
    query = "scsearch:" + query
url = "http://127.0.0.1:2333/v4/loadtracks?identifier=" + urllib.parse.quote(query, safe="")
request = urllib.request.Request(url, headers={"Authorization": "local-test-only"})
start = time.monotonic()
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
except (urllib.error.URLError, TimeoutError) as exc:
    print("Lavalink unavailable or request failed:", exc)
    sys.exit(1)
elapsed = time.monotonic() - start
tracks = data.get("data", [])
if isinstance(tracks, dict):
    tracks = tracks.get("tracks", [tracks])
print(f"Load type: {data.get('loadType')} | Time: {elapsed:.2f}s | Tracks: {len(tracks)}")
for item in tracks[:5]:
    info = item.get("info", {})
    print("-", info.get("title"), "|", info.get("author"), "|", info.get("uri"))
