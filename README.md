# DungeonHub

Браузерный чат для текстовых ролевых игр с друзьями, где мастером выступает нейросеть (Gemini).

- Комнаты по ссылке, без регистрации.
- Заявки игроков собираются в один ход мастера.
- Длинные заявки: «начало заявки» … «конец заявки».
- Кубики бросает сервер (`/roll 1d20+3`), а не нейросеть.
- Панель мастера: мир, стартовая ситуация, секретный сюжет, стиль, длина ответов, режим «повествование / механика».
- Листы персонажей, которые мастер учитывает в каждом ответе.
- Автоматическая «летопись»: старые события сжимаются в краткое содержание, чтобы мастер помнил всю кампанию.

---

## Запуск на сервере (VM)

Инструкция для Ubuntu/Debian. Все команды выполняются в терминале сервера.

### 1. Установить Docker (один раз)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

После этого выйдите с сервера и зайдите снова, чтобы права применились.

### 2. Скачать проект

```bash
git clone https://github.com/Ccheshir/DungeonHub.git
cd DungeonHub
```

Если репозиторий приватный, git попросит логин и токен GitHub (Settings → Developer settings → Personal access tokens).

### 3. Вписать ключ Gemini

```bash
cp .env.example .env
nano .env
```

Вставьте ключ после `GEMINI_API_KEY=`, по желанию задайте `CREATE_ROOM_PASSWORD`. Сохранить: `Ctrl+O`, `Enter`, выйти: `Ctrl+X`.

### 4. Запустить

```bash
docker compose up -d --build
```

Сайт откроется по адресу `http://IP-СЕРВЕРА:8000`. Если не открывается, разрешите порт в файрволе:

```bash
sudo ufw allow 8000/tcp
```

Также проверьте, что порт 8000 открыт в панели провайдера VM (группы безопасности / firewall).

### Полезные команды

| Что сделать | Команда |
|---|---|
| Посмотреть логи | `docker compose logs -f` |
| Обновить до новой версии | `git pull && docker compose up -d --build` |
| Перезапустить | `docker compose restart` |
| Остановить | `docker compose down` |
| Прогнать тесты | `docker compose run --rm dungeonhub python -m unittest discover tests` |

Все данные (комнаты, история, летописи) лежат в папке `data/`. Для резервной копии достаточно скопировать её.

---

## Как играть

1. На главной создайте комнату. Вы автоматически становитесь её мастером-настройщиком.
2. Откройте **⚙ Настройки**: опишите мир, стартовую ситуацию и секретные заметки о сюжете.
3. Нажмите **🔗 Пригласить** и отправьте ссылку друзьям.
4. Каждый заходит, вводит имя и персонажа, заполняет **📜 Мой персонаж**.
5. Нажмите **⏭ Ход мастера**: нейросеть опишет вступительную сцену.

### Команды в чате

| Команда | Что делает |
|---|---|
| обычное сообщение | заявка: что делает персонаж |
| `начало заявки` … `конец заявки` | заявка из нескольких сообщений, мастер ждёт её окончания |
| `/roll 1d20+3 внимательность` | бросок кубиков (`/r`, `/к`, `/бросок` тоже работают) |
| `/ooc текст` или `((текст))` | разговор вне игры, мастер не считает это действием |
| `/ход` | передать ход мастеру сейчас (закрывает открытые заявки) |
| `/help` | подсказка |

Мастер отвечает через несколько секунд после последней заявки (пауза настраивается), чтобы собрать заявки всех игроков в одну сцену. Если кто-то забыл написать «конец заявки», она закроется сама по таймауту.

### Ссылка мастера

Настройки доступны только с того устройства, где создана комната. Чтобы открыть их с другого устройства, в **🔗 Пригласить** скопируйте «ссылку мастера». Игрокам её не давайте: в ней секретные сюжетные заметки.

---

## Устройство проекта

```
app/
  main.py      — веб-сервер: HTTP API и WebSocket
  room.py      — движок комнаты: заявки, ходы мастера, летопись (не зависит от интерфейса)
  prompts.py   — системный промпт мастера и сборка истории
  llm.py       — клиент Gemini и заглушка для проверки без ключа
  dice.py      — кубики
  db.py        — SQLite
  static/      — интерфейс (HTML/CSS/JS без сборки)
tests/         — тесты движка
```

Движок комнаты общается с клиентами через простой интерфейс `send(dict)`, поэтому Discord- или Telegram-бота можно добавить поверх него, не трогая логику игры.

## Запуск на VM без Docker

Подходит, если Docker на VPS не стартует с ошибкой `ip_unprivileged_port_start ... permission denied` (так бывает на VPS, сделанных из LXC-контейнеров).

```bash
sudo apt update && sudo apt install -y python3-venv
cd ~/DungeonHub
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Автозапуск через systemd (файл `.env` должен быть заполнен):

```bash
sudo tee /etc/systemd/system/dungeonhub.service > /dev/null <<EOF
[Unit]
Description=DungeonHub
After=network-online.target

[Service]
User=$USER
WorkingDirectory=$HOME/DungeonHub
EnvironmentFile=$HOME/DungeonHub/.env
Environment=DB_PATH=$HOME/DungeonHub/data/dungeonhub.db
ExecStart=$HOME/DungeonHub/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
Restart=always

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now dungeonhub
```

Логи: `journalctl -u dungeonhub -f`. Обновление: `git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart dungeonhub`.

## Локальный запуск без Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
GEMINI_API_KEY=... uvicorn app.main:app --reload
```
