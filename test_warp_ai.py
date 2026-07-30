import os
import sys
import socket
import json
import urllib.request
import urllib.parse

# Import config if present
try:
    import config
    GEMINI_API_KEY = getattr(config, "GEMINI_API_KEY", "")
except Exception:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

def check_socket(host="127.0.0.1", port=4001):
    print(f"📡 1. Проверка порта WARP прокси ({host}:{port})...")
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"   ✅ Порт {port} открыт и принимает соединения!")
            return True
    except Exception as e:
        print(f"   ❌ Ошибка подключения к порту {port}: {e}")
        return False

def check_ip(proxy_url=None):
    mode = f"через прокси ({proxy_url})" if proxy_url else "Прямое подключение"
    print(f"🌐 Проверка внешнего IP ({mode})...")
    try:
        req = urllib.request.Request("https://ifconfig.me/all.json", headers={"User-Agent": "curl/7.68.0"})
        if proxy_url:
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        with opener.open(req, timeout=5) as res:
            data = json.loads(res.read().decode("utf-8"))
            ip = data.get("ip_addr", "N/A")
            country = data.get("country", "N/A")
            print(f"   📍 IP: {ip} (Страна: {country})")
    except Exception as e:
        print(f"   ⚠️ Не удалось определить IP ({e})")

def test_gemini_model(model_name, api_key, proxy_url=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": "Hi, reply OK"}]}]
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    if proxy_url:
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    try:
        with opener.open(req, timeout=10) as res:
            res_data = json.loads(res.read().decode("utf-8"))
            candidates = res_data.get("candidates", [])
            if candidates:
                return True, "200 OK"
            return False, "No candidates"
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")[:120].replace("\n", " ")
        return False, f"HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, f"Error: {e}"

def main():
    print("=" * 60)
    print("🤖 ДИАГНОСТИКА СВЯЗИ С GEMINI AI & CLOUDFLARE WARP PROXY")
    print("=" * 60)

    if not GEMINI_API_KEY:
        print("❌ GEMINI_API_KEY не найден в config.py или переменных окружения!")
        return

    warp_alive = check_socket("127.0.0.1", 4001)
    proxy_url = "http://127.0.0.1:4001" if warp_alive else None

    print("\n" + "-" * 60)
    check_ip(proxy_url=None)
    if warp_alive:
        check_ip(proxy_url="http://127.0.0.1:4001")

    print("\n" + "-" * 60)
    print("🧪 2. Тестирование отклика моделей Gemini...")
    print("-" * 60)

    for m in MODELS:
        ok, details = test_gemini_model(m, GEMINI_API_KEY, proxy_url=proxy_url)
        status_icon = "✅" if ok else "❌"
        mode_str = f"WARP proxy" if proxy_url else "Direct"
        print(f"{status_icon} Model [{m}] ({mode_str}): {details}")

    print("\n" + "=" * 60)
    print("🏁 Диагностика завершена!")
    print("=" * 60)

if __name__ == "__main__":
    main()
