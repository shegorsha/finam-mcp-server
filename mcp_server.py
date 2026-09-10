import os, json, time, asyncio
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn, requests

app = FastAPI()
BASE_DIR = os.path.expanduser("~/smc_web")
SECRETS_FILE = os.path.join(BASE_DIR, "secrets.json")
ALERTS_FILE = os.path.join(BASE_DIR, "alerts.json")

_s = {}
if os.path.exists(SECRETS_FILE):
    with open(SECRETS_FILE, "r", encoding="utf-8") as f:
        _s = json.load(f)
FINAM_API_KEY = _s.get("finam_api_key", "")
YANDEX_API_KEY = _s.get("yandex_api_key", "")
YANDEX_FOLDER_ID = _s.get("yandex_folder_id", "")

_jwt = None
_jwt_exp = 0

def get_finam_jwt():
    global _jwt, _jwt_exp
    if _jwt and time.time() < _jwt_exp:
        return _jwt
    resp = requests.post("https://api.finam.ru/v1/sessions",
                         json={"secret": FINAM_API_KEY}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _jwt = data.get("token", "")
    if not _jwt:
        raise RuntimeError(f"Finam auth failed: {data}")
    _jwt_exp = time.time() + 14 * 60
    return _jwt

def _val(item, key):
    v = item.get(key)
    if isinstance(v, dict):
        return float(v.get("value", 0))
    return float(v or 0)

TF_MAP = {"M15": "TIME_FRAME_M15", "H1": "TIME_FRAME_H1",
          "H4": "TIME_FRAME_H4", "D1": "TIME_FRAME_D"}

def fetch_candles(symbol, tf, count=50):
    jwt = get_finam_jwt()
    timeframe = TF_MAP.get(tf, "TIME_FRAME_H1")
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)
    url = f"https://api.finam.ru/v1/instruments/{symbol}/bars"
    resp = requests.get(url, headers={"Authorization": f"Bearer {jwt}"},
                        params={"timeframe": timeframe,
                                "interval.start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "interval.end_time": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
                        timeout=15)
    resp.raise_for_status()
    raw = resp.json()
    items = raw.get("bars", []) if isinstance(raw, dict) else raw
    candles = []
    for item in items:
        candles.append({
            "ts": str(item.get("timestamp", "")),
            "open": _val(item, "open"), "high": _val(item, "high"),
            "low": _val(item, "low"), "close": _val(item, "close"),
            "volume": _val(item, "volume"),
        })
    return candles[-count:]

def call_yandexgpt(system_prompt, user_prompt):
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.3, "maxTokens": 2000},
        "messages": [{"role": "system", "text": system_prompt},
                     {"role": "user", "text": user_prompt}]
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    alts = result.get("result", {}).get("alternatives", [])
    if not alts:
        raise RuntimeError("YandexGPT: empty response")
    return alts[0].get("message", {}).get("text", "")

def extract_json_array(text):
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        return json.loads(text[start:end+1])
    except:
        return []

def load_alerts():
    if not os.path.exists(ALERTS_FILE):
        return []
    try:
        with open(ALERTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_alerts(alerts):
    with open(ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)

# MCP Protocol endpoints (FastAPI style)
@app.post("/tools/list_accounts")
async def tool_list_accounts():
    def _do():
        jwt = get_finam_jwt()
        resp = requests.get("https://api.finam.ru/v1/accounts",
                            headers={"Authorization": f"Bearer {jwt}"}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"HTTP {resp.status_code}", "hint": "Найдите account_id в терминале Финам или личном кабинете"}
    res = await asyncio.to_thread(_do)
    return {"result": json.dumps(res, ensure_ascii=False)}

@app.post("/tools/get_portfolio")
async def tool_get_portfolio(request: Request):
    data = await request.json()
    account_id = data.get("account_id")
    def _do(acc_id):
        jwt = get_finam_jwt()
        resp = requests.get(f"https://api.finam.ru/v1/accounts/{acc_id}",
                            headers={"Authorization": f"Bearer {jwt}"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = {
            "account_id": acc_id,
            "equity": _val(data, "equity"),
            "free_margin": _val(data, "freeMargin"),
            "positions": []
        }
        for pos in data.get("positions", []):
            result["positions"].append({
                "symbol": str(pos.get("symbol", "")),
                "quantity": _val(pos, "quantity"),
                "avg_price": _val(pos, "averagePrice"),
                "current_price": _val(pos, "currentPrice"),
                "pnl": _val(pos, "unrealizedPnL"),
            })
        return result
    res = await asyncio.to_thread(_do, account_id)
    return {"result": json.dumps(res, ensure_ascii=False)}

@app.post("/tools/get_candles")
async def tool_get_candles(request: Request):
    data = await request.json()
    ticker = data.get("ticker", "")
    tf = data.get("tf", "H1")
    count = int(data.get("count", 50))
    count = min(count, 200)
    symbol = ticker if "@" in ticker else f"{ticker}@MISX"
    candles = await asyncio.to_thread(fetch_candles, symbol, tf, count)
    return {"result": json.dumps(candles, ensure_ascii=False)}

@app.post("/tools/get_quote")
async def tool_get_quote(request: Request):
    data = await request.json()
    ticker = data.get("ticker", "")
    symbol = ticker if "@" in ticker else f"{ticker}@MISX"
    def _do(sym):
        jwt = get_finam_jwt()
        resp = requests.get(f"https://api.finam.ru/v1/instruments/{sym}/quotes/latest",
                            headers={"Authorization": f"Bearer {jwt}"}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        last = data.get("last", data)
        return {"symbol": sym, "price": _val(last, "value"), "ts": data.get("timestamp", "")}
    res = await asyncio.to_thread(_do, symbol)
    return {"result": json.dumps(res, ensure_ascii=False)}

@app.post("/tools/analyze_smc")
async def tool_analyze_smc(request: Request):
    data = await request.json()
    ticker = data.get("ticker", "")
    tf = data.get("tf", "H1")
    symbol = ticker if "@" in ticker else f"{ticker}@MISX"
    def _do(sym, tframe):
        candles = fetch_candles(sym, tframe)
        last_price = candles[-1]["close"]
        candle_text = "\n".join(
            f"{c['ts']} | O:{c['open']} H:{c['high']} L:{c['low']} C:{c['close']} V:{c['volume']}"
            for c in candles
        )
        system_prompt = (
            "Ты — торговый помощник по концепции Smart Money. Анализируешь ценовое действие "
            "и даёшь торговые рекомендации на русском языке. "
            "В конце ответа обязательно добавь JSON-блок уровней в формате: "
            '[{"type": "Order Block", "price": 123.45, "description": "...", "status": "active"}, ...]. '
            "Не пиши ничего после этого JSON."
        )
        user_prompt = (
            "Проанализируй и найди:\n"
            "- свежие Order Blocks\n- незакрытые Fair Value Gaps\n"
            "- Liquidity Sweeps\n- BOS или CHoCH\n\n"
            "Дай рекомендацию: вход, стоп, тейк, R:R, объяснение.\n\n"
            f"Инструмент: {sym}, tf: {tframe}, цена: {last_price}\n\n"
            f"Свечи:\n{candle_text}"
        )
        analysis = call_yandexgpt(system_prompt, user_prompt)
        levels = extract_json_array(analysis)
        return {"ticker": ticker, "tf": tframe, "last_price": last_price,
                "analysis": analysis, "levels": levels}
    res = await asyncio.to_thread(_do, symbol, tf)
    return {"result": json.dumps(res, ensure_ascii=False)}

@app.post("/tools/calc_position_size")
async def tool_calc_position_size(request: Request):
    data = await request.json()
    equity = float(data.get("equity", 0))
    risk_percent = float(data.get("risk_percent", 1))
    entry = float(data.get("entry", 0))
    stop = float(data.get("stop", 0))
    take_profit = float(data.get("take_profit", 0)) if data.get("take_profit") else 0
    risk_amount = equity * (risk_percent / 100.0)
    distance = abs(entry - stop)
    if distance == 0:
        return JSONResponse({"error": "Entry and stop cannot be the same"}, status_code=400)
    qty = risk_amount / distance
    rr = 0
    if take_profit > 0:
        rr = abs(take_profit - entry) / distance
    return {
        "result": json.dumps({
            "equity": equity, "risk_percent": risk_percent,
            "risk_amount": round(risk_amount, 2),
            "entry": entry, "stop": stop,
            "take_profit": take_profit if take_profit > 0 else None,
            "quantity": round(qty, 0),
            "risk_reward": round(rr, 2) if rr > 0 else None,
            "position_value": round(qty * entry, 2),
        }, ensure_ascii=False)
    }

@app.post("/tools/set_alert")
async def tool_set_alert(request: Request):
    data = await request.json()
    ticker = data.get("ticker", "")
    price = float(data.get("price", 0))
    condition = data.get("condition", "above")
    symbol = ticker if "@" in ticker else f"{ticker}@MISX"
    alerts = load_alerts()
    alert = {"symbol": symbol, "price": price, "condition": condition,
             "created_at": datetime.now(timezone.utc).isoformat(), "fired": False}
    alerts.append(alert)
    save_alerts(alerts)
    return {"result": json.dumps({"ok": True, "alert": alert}, ensure_ascii=False)}

@app.post("/tools/list_alerts")
async def tool_list_alerts():
    alerts = load_alerts()
    return {"result": json.dumps(alerts, ensure_ascii=False)}

# MCP Discovery endpoint
@app.get("/.well-known/mcp")
async def mcp_discovery():
    return {
        "name": "Finam Trading Assistant",
        "description": "MCP server for Finam API + Smart Money analysis via YandexGPT",
        "tools": [
            {"name": "list_accounts", "description": "Получить список торговых счетов"},
            {"name": "get_portfolio", "description": "Получить портфель по account_id"},
            {"name": "get_candles", "description": "Получить OHLCV свечи"},
            {"name": "get_quote", "description": "Последняя цена по тикеру"},
            {"name": "analyze_smc", "description": "Smart Money анализ через YandexGPT"},
            {"name": "calc_position_size", "description": "Расчёт объёма позиции по риску"},
            {"name": "set_alert", "description": "Установить алерт по цене"},
            {"name": "list_alerts", "description": "Показать все алерты"}
        ]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8083)
