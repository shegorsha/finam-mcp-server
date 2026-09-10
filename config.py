import json
import os

CONFIG_FILE = os.path.expanduser("~/smc_web/tickers.json")

DEFAULT_TICKERS = [
    {"short": "Si",   "full": "SiU6@RTSX",   "name": "Доллар-рубль"},
    {"short": "MXI",  "full": "MXU6@RTSX",   "name": "Индекс МосБиржи (мини)"},
    {"short": "RTS",  "full": "RTSI@MISX",   "name": "Индекс РТС"},
    {"short": "BR",   "full": "BRV6@RTSX",   "name": "Нефть Brent"},
    {"short": "GOLD", "full": "GDU6@RTSX",   "name": "Золото"},
    {"short": "SBER", "full": "SBER@MISX",   "name": "Сбербанк"},
    {"short": "GAZP", "full": "GAZP@MISX",   "name": "Газпром"},
    {"short": "LKOH", "full": "LKOH@MISX",   "name": "Лукойл"},
    {"short": "GMKN", "full": "GMKN@MISX",   "name": "Норникель"},
    {"short": "VTBR", "full": "VTBR@MISX",   "name": "ВТБ"},
]

def load_tickers():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    save_tickers(DEFAULT_TICKERS)
    return DEFAULT_TICKERS

def save_tickers(tickers):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(tickers, f, ensure_ascii=False, indent=2)

def get_ticker_map():
    tickers = load_tickers()
    return {t["short"]: t for t in tickers}
