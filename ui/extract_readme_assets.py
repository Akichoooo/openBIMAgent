"""从 GitHub README 提取截图/演示链接（UI 调研用, 单次使用）。

用法: python extract_readme_assets.py owner/repo [owner/repo ...]
"""
import json
import re
import sys
import urllib.request

IMG = re.compile(r"https?://[^\s)\"']+\.(?:png|jpg|jpeg|gif|webp)")
URL = re.compile(r"https?://[^\s)\"']+")


def fetch(repo: str) -> None:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/readme",
        headers={"Accept": "application/vnd.github.raw", "User-Agent": "obm-ui-research"},
    )
    try:
        t = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    except Exception as exc:
        print(f"===== {repo} ===== FETCH FAIL: {exc}")
        return
    print(f"===== {repo} ===== README {len(t)} chars")
    meta_req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}", headers={"User-Agent": "obm-ui-research"}
    )
    try:
        meta = json.load(urllib.request.urlopen(meta_req, timeout=25))
        home = meta.get("homepage") or ""
        if home:
            print("  官网:", home)
    except Exception:
        pass
    for i in list(dict.fromkeys(IMG.findall(t)))[:8]:
        print("  截图:", i[:130])
    rel = re.findall(r"!\[[^\]]*\]\(([^):]+?\.(?:png|jpg|jpeg|gif|webp))\)", t)
    for r in list(dict.fromkeys(rel))[:8]:
        print("  截图(raw):", f"https://raw.githubusercontent.com/{repo}/HEAD/{r}"[:150])
    links = [
        u
        for u in dict.fromkeys(URL.findall(t))
        if any(k in u.lower() for k in ("demo", "youtu", "vercel", "pages.dev", "web.app"))
        and not u.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
    ]
    for u in links[:5]:
        print("  演示:", u[:130])


if __name__ == "__main__":
    for r in sys.argv[1:]:
        fetch(r)
