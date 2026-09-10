# Finam MCP + YandexGPT — документация проекта

## Что это
Веб-приложение на FastAPI: запрашивает данные счёта у Finam MCP, передаёт в YandexGPT, возвращает понятный текст про баланс.

## Сервер (VPS)
- IP: 217.149.25.181
- ОС: Ubuntu 22.04.5 LTS
- Python: 3.10
- Пользователь: root

## Что установлено
- fastapi 0.115.6, uvicorn 0.34.0, requests 2.25.1
- Docker (для Finam MCP)

## Структура файлов
/root/web_app/main.py — основной код
/etc/systemd/system/finam-bot.service — служба автозапуска

## Порты
- 8000 — веб-приложение (FastAPI/uvicorn)
- 8085 — Finam MCP (Docker-контейнер)

## Ключи и токены (долгоживущий API-ключ)
- YANDEX_API_KEY=YOUR_YANDEX_API_KEY
- YANDEX_FOLDER_ID=YOUR_FOLDER_ID
- Ключи вшиты в systemd-службу

## Схема работы
Браузер → POST /balance → FastAPI (порт 8000)
  → requests.post("localhost:8085/get_account_info") → Finam MCP → Finam API
  → JSON с данными счёта
  → Формирует промпт → requests.post("llm.api.cloud.yandex.net") → YandexGPT
  → Текстовый ответ → JSON {"message": "..."} → Браузер

## Описание кода (web_app/main.py)
- get_mcp_data(): POST на localhost:8085/get_account_info, возвращает JSON
- call_yandex_gpt(): авторизация "Api-Key" (НЕ Bearer), модель yandexgpt/latest, temperature 0.3, ответ из alternatives[0]["message"]["text"]
- GET / — HTML с кнопкой
- POST /balance — возвращает {"message": "ответ GPT"}
- В HTML: fetch с {method: 'POST', headers: Content-Type, body: '{}'}, показывает "Загрузка..."

## Systemd-служба
[Unit]
Description=Finam MCP Web Bot
After=network.target docker.service
[Service]
Type=simple
WorkingDirectory=/root
Environment=YC_API_KEY=YOUR_YANDEX_API_KEY
Environment=YC_FOLDER_ID=YOUR_FOLDER_ID
ExecStart=/usr/local/bin/uvicorn web_app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target

## Управление
- systemctl status finam-bot — статус
- systemctl restart finam-bot — перезапуск
- systemctl stop finam-bot — остановка
- journalctl -u finam-bot -f — логи в реальном времени
- lsof -i :8000 — проверить порт

## История ошибок и решений
1. Could not import module "web_app" → запускать из /root, команда: uvicorn web_app.main:app
2. Could not import module "main" → файл в папке web_app/, писать web_app.main:app
3. address already in use → kill -9 $(lsof -t -i :8000)
4. 405 Method Not Allowed → fetch отправлял GET, нужен POST
5. &quot; в ответе GPT → использовать json.dumps() вместо ручного экранирования
6. Не найден IAM-токен → сменить заголовок с Bearer на Api-Key
7. alternatives не список → добавить индекс [0]: alternatives[0]["message"]["text"]

## Что можно добавить дальше
1. Полноценный MCP-сервер (ИИ сам вызывает инструменты Finam)
2. Новые эндпоинты: /positions, /orders, /history
3. Интеграция с Open WebUI (чат вместо кнопки)
4. Перенос токенов в .env с правами 600
5. Healthcheck и мониторинг
