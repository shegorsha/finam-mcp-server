from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
import os, json, time, asyncio
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv

# 1. Инициализация и проверка ключей
load_dotenv()

API_SECRET_KEY = os.getenv("API_SECRET_KEY")
if not API_SECRET_KEY:
    raise RuntimeError("API_SECRET_KEY не найден! Проверь файл .env")

app = FastAPI()

def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key or x_api_key != API_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Неверный API ключ")
    return True

# --- Конфигурация путей и загрузка секретов ---
BASE_DIR = os.path.expanduser("~/smc_web")
CONFIG_FILE = os.path.join(BASE_DIR, "tickers.json")
SECRETS_FILE = os.path.join(BASE_DIR, "secrets.json")
ALERTS_FILE = os.path.join(BASE_DIR, "alerts.json")

_s = {}
if os.path.exists(SECRETS_FILE):
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            _s = json.load(f)
    except Exception:
        pass

FINAM_API_KEY = _s.get("finam_api_key", "")
YANDEX_API_KEY = _s.get("yandex_api_key", "")
YANDEX_FOLDER_ID = _s.get("yandex_folder_id", "")

DEFAULT_TICKERS = [
    {"short": "Si", "full": "SiU6@RTSX", "name": "Доллар-рубль"},
    {"short": "MXI", "full": "MXU6@RTSX", "name": "Индекс МосБиржи (мини)"},
    {"short": "RTS", "full": "RTSI@MISX", "name": "Индекс РТС"},
    {"short": "BR", "full": "BRV6@RTSX", "name": "Нефть Brent"},
    {"short": "GOLD", "full": "GDU6@RTSX", "name": "Золото"},
    {"short": "SBER", "full": "SBER@MISX", "name": "Сбербанк"},
    {"short": "GAZP", "full": "GAZP@MISX", "name": "Газпром"},
    {"short": "LKOH", "full": "LKOH@MISX", "name": "Лукойл"},
    {"short": "GMKN", "full": "GMKN@MISX", "name": "Норникель"},
    {"short": "VTBR", "full": "VTBR@MISX", "name": "ВТБ"},
]

# --- Логика работы с данными ---
def load_tickers():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    save_tickers(DEFAULT_TICKERS)
    return DEFAULT_TICKERS

def save_tickers(tickers):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(tickers, f, ensure_ascii=False, indent=2)

def get_ticker_map():
    return {t["short"]: t for t in load_tickers()}

_jwt = None
_jwt_exp = 0

def get_finam_jwt():
    global _jwt, _jwt_exp
    if _jwt and time.time() < _jwt_exp:
        return _jwt
    if not FINAM_API_KEY:
        raise RuntimeError("FINAM_API_KEY не найден в secrets.json")
    resp = requests.post(
        "https://api.finam.ru/v1/sessions",
        json={"secret": FINAM_API_KEY},
        timeout=10
    )
    resp.raise_for_status()
    data = resp.json()
    _jwt = data.get("token", "")
    _jwt_exp = time.time() + 14 * 60
    return _jwt

def _val(item, key):
    v = item.get(key)
    if v is None:
        return 0.0
    if isinstance(v, dict):
        sub = v.get("value")
        return float(sub) if sub is not None else 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0

TF_MAP = {
    "M15": "TIME_FRAME_M15",
    "H1": "TIME_FRAME_H1",
    "H4": "TIME_FRAME_H4",
    "D1": "TIME_FRAME_D"
}

def fetch_candles(symbol, tf, count=50):
    jwt = get_finam_jwt()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)
    resp = requests.get(
        f"https://api.finam.ru/v1/instruments/{symbol}/bars",
        headers={"Authorization": f"Bearer {jwt}"},
        params={
            "timeframe": TF_MAP.get(tf, "TIME_FRAME_H1"),
            "interval.start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval.end_time": now.strftime("%Y-%m-%dT%H:%M:%SZ")
        },
        timeout=15
    )
    resp.raise_for_status()
    items = resp.json().get("bars", [])
    candles = [
        {
            "ts": str(i.get("timestamp", "")),
            "open": _val(i, "open"),
            "high": _val(i, "high"),
            "low": _val(i, "low"),
            "close": _val(i, "close"),
            "volume": _val(i, "volume")
        }
        for i in items
    ]
    return candles[-count:]

def call_yandexgpt(system_prompt, user_prompt):
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        raise RuntimeError("Yandex credentials missing")
    resp = requests.post(
        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
        headers={
            "Authorization": f"Api-Key {YANDEX_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
            "completionOptions": {"stream": False, "temperature": 0.3, "maxTokens": 2000},
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_prompt}
            ]
        },
        timeout=60
    )
    resp.raise_for_status()
    alts = resp.json().get("result", {}).get("alternatives", [])
    return alts[0].get("message", {}).get("text", "") if alts else ""

def parse_levels(text):
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start:end+1])
        if not isinstance(data, list):
            return []
        return [
            {
                "type": l.get("type", "Support/Resistance"),
                "price": float(l.get("price", 0)),
                "description": l.get("description", "-"),
                "status": l.get("status", "active")
            }
            for l in data
        ]
    except Exception:
        return []

def load_alerts():
    if not os.path.exists(ALERTS_FILE):
        return []
    try:
        with open(ALERTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_alerts(alerts):
    os.makedirs(os.path.dirname(ALERTS_FILE), exist_ok=True)
    with open(ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)

# --- Фоновый процесс алертов ---
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(alert_poller())

async def alert_poller():
    while True:
        await asyncio.sleep(60)
        alerts = load_alerts()
        if not alerts:
            continue
        fired = []
        for a in alerts:
            if a.get("fired"):
                continue
            try:
                jwt = get_finam_jwt()
                resp = requests.get(
                    f"https://api.finam.ru/v1/instruments/{a['symbol']}/quotes/latest",
                    headers={"Authorization": f"Bearer {jwt}"},
                    timeout=5
                )
                data = resp.json()
                price = _val(data.get("last", {}), "value")
                hit = False
                if a["condition"] == "above" and price >= a["price"]:
                    hit = True
                elif a["condition"] == "below" and price <= a["price"]:
                    hit = True
                if hit:
                    a["fired"] = True
                    a["fired_price"] = price
                    a["fired_at"] = datetime.now(timezone.utc).isoformat()
                    fired.append(a)
                    print(f"[ALERT] {a['symbol']} {a['condition']} {a['price']} -> {price}")
            except Exception as e:
                print(f"[ALERT ERROR] {a.get('symbol')}: {e}")
        if fired:
            save_alerts(alerts)

# --- Эндпоинты ---

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <title>Торговый ассистент (SMC)</title>
    </head>
    <body style="font-family: sans-serif; padding: 2rem;">
        <h1>Торговый ассистент (Smart Money)</h1>
        <p>Статус API ключа: <span style="color: green;">OK</span></p>
        <p>Нажмите кнопку, чтобы получить баланс:</p>
        <button id="getBalanceBtn" style="padding: 10px 20px; font-size: 16px; background-color: #007bff; color: white; border: none; cursor: pointer;">Get Balance</button>
        <div id="result" style="margin-top: 20px; padding: 10px; background: #f0f0f0;"></div>
        <script>
            const apiKey = "{API_SECRET_KEY}";
            document.getElementById('getBalanceBtn').addEventListener('click', async () => {{
                const res = await fetch('/balance', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-API-Key': apiKey
                    }}
                }});
                if (res.status === 401) {{
                    document.getElementById('result').innerText = 'Ошибка: Неверный API ключ';
                    return;
                }}
                if (!res.ok) {{
                    document.getElementById('result').innerText = 'Ошибка: ' + res.status;
                    return;
                }}
                const data = await res.json();
                document.getElementById('result').innerHTML = `Баланс: ${{data.raw_data.balance}} руб. Убыток: ${{data.raw_data.loss}} руб.`;
            }});
        </script>
    </body>
    </html>
    """

@app.post("/balance", dependencies=[Depends(verify_api_key)])
async def get_balance():
    return {
        "message": "Баланс: 15000.5 руб. Убыток: 0.0 руб.",
        "raw_data": {
            "balance": 15000.5,
            "loss": 0.0,
            "currency": "RUB"
        }
    }

@app.get("/api/tickers")
async def api_tickers():
    return load_tickers()

@app.post("/api/tickers")
async def api_save_tickers(request: Request):
    data = await request.json()
    save_tickers(data)
    return {"ok": True}

@app.get("/api/alerts")
async def api_alerts():
    return load_alerts()

@app.get("/api/signal")
async def get_signal(ticker: str, tf: str = "H1"):
    info = get_ticker_map().get(ticker)
    if not info:
        return JSONResponse({"error": "Ticker not found"}, status_code=404)
    try:
        candles = fetch_candles(info["full"], tf)
    except Exception as e:
        return JSONResponse({"error": f"Finam: {e}"}, status_code=502)
    last_price = candles[-1]["close"]
    ct = "\n".join(
        f"{c['ts']} O:{c['open']} H:{c['high']} L:{c['low']} C:{c['close']} V:{c['volume']}"
        for c in candles
    )
    sp = (
        "Ты торговый помощник по Smart Money. В конце ответа добавь JSON уровней: "
        '[{"type":"Order Block","price":123.45,"description":"...","status":"active"}]. Не пиши после JSON.'
    )
    up = (
        f"Анализ: OB, FVG, Liquidity, BOS/CHoCH. Рекомендация: вход, стоп, тейк, R:R.\n"
        f"Инструмент: {info['full']}, tf: {tf}, цена: {last_price}\n\n{ct}"
    )
    try:
        analysis_text = call_yandexgpt(sp, up)
    except Exception as e:
        return JSONResponse({"error": f"YandexGPT: {e}"}, status_code=502)
    return {
        "ticker": ticker,
        "tf": tf,
        "bars": len(candles),
        "last_price": last_price,
        "last_time": candles[-1]["ts"],
        "analysis": analysis_text,
        "levels": parse_levels(analysis_text)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
