import base64
import json
import urllib.request
import urllib.parse
from pathlib import Path

mmd_path = Path("architecture.mmd")
output_path = Path("architecture.png")

mmd_code = mmd_path.read_text(encoding="utf-8")

# mermaid.ink expects a JSON payload encoded with base64url
def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

payload = {
    "code": mmd_code,
    "mermaid": {"theme": "default"},
}
encoded = base64url_encode(json.dumps(payload).encode("utf-8"))
url = f"https://mermaid.ink/img/{encoded}?type=png"

print(f"Downloading from: {url[:120]}...")
req = urllib.request.Request(
    url,
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    },
)
with urllib.request.urlopen(req, timeout=60) as resp:
    output_path.write_bytes(resp.read())

print(f"Saved: {output_path} ({output_path.stat().st_size} bytes)")
