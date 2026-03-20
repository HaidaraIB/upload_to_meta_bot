# Ubuntu Guide: Run Telegram Bot with systemd

This guide shows the simplest production setup for Python Telegram bots on Ubuntu VPS without using `screen`.

## Why this is better than screen

- Auto-starts bot after VPS reboot
- Auto-restarts bot after crashes
- Centralized logs with `journalctl`
- Simple daily management with `systemctl`

## 1) Connect to your VPS

```bash
ssh your_user@your_vps_ip
```

## 2) Install required packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

## 3) Go to bot project folder

```bash
cd /path/to/upload_to_meta_bot
```

## 4) Create virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

## 5) Prepare environment variables

Your project reads `.env` using `load_dotenv()`, so keep a valid `.env` file in the project root.

If you have `.env.prod`:

```bash
cp .env.prod .env
```

Make sure `.env` includes values like:

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `OWNER_ID`
- any DB/Meta variables your bot needs

## 6) Create a systemd service

Create the service file:

```bash
sudo nano /etc/systemd/system/upload_to_meta_bot.service
```

Paste and edit paths/user:

```ini
[Unit]
Description=Upload To Meta Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/upload_to_meta_bot
ExecStart=/path/to/upload_to_meta_bot/.venv/bin/python /path/to/upload_to_meta_bot/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Save and exit.

## 7) Enable and start service

```bash
sudo systemctl daemon-reload
sudo systemctl enable upload_to_meta_bot
sudo systemctl start upload_to_meta_bot
```

## 8) Check service status

```bash
sudo systemctl status upload_to_meta_bot
```

Expected result: `active (running)`.

## 9) View logs

```bash
sudo journalctl -u upload_to_meta_bot -f
```

## Daily commands

Restart:

```bash
sudo systemctl restart upload_to_meta_bot
```

Stop:

```bash
sudo systemctl stop upload_to_meta_bot
```

Start:

```bash
sudo systemctl start upload_to_meta_bot
```

Disable auto-start:

```bash
sudo systemctl disable upload_to_meta_bot
```

## Multi-bot pattern (simple)

For each bot:

1. Keep each bot in its own folder
2. Create its own `.venv`
3. Create a separate service file with a unique name, for example:
   - `bot_a.service`
   - `bot_b.service`
4. Run `daemon-reload`, `enable`, and `start` for each service

This gives you reliable production behavior with minimal complexity.
