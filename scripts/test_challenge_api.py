import urllib.request
import json
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

urls_to_test = [
    "https://challenge.place/api/challenges/6a0760ac3f19d9f88ec784d2",
    "https://challenge.place/api/stages/6a07623b40f3cd059c27ed23",
    "https://challenge.place/api/v1/challenges/6a0760ac3f19d9f88ec784d2",
    "https://api.challengeplace.com/challenges/6a0760ac3f19d9f88ec784d2",
    "https://challenge.place/c/6a0760ac3f19d9f88ec784d2/stage/6a07623b40f3cd059c27ed23"
]

for url in urls_to_test:
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as res:
            content = res.read().decode('utf-8')
            print(f"URL: {url} -> Status 200, length: {len(content)}")
            if content.startswith('{') or content.startswith('['):
                print(f"  JSON payload keys: {list(json.loads(content).keys()) if isinstance(json.loads(content), dict) else 'list'}")
            else:
                # search for __NEXT_DATA__ or initial state or json in HTML
                matches = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
                for m in matches:
                    if 'stage' in m.lower() or 'match' in m.lower() or 'round' in m.lower():
                        print(f"  Found script tag with relevant data, length: {len(m)}")
    except Exception as e:
        print(f"URL: {url} -> Failed: {e}")
