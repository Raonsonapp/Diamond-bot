# Oracle Cloud Always Free — Diamond Bot

This guide runs the existing Python Telegram bot continuously on a small Ubuntu VM. It uses Telegram polling; no public HTTP port is required for ordinary bot operation.

## Important before starting

- Oracle Always Free resources are free only within the provider's published limits and account rules. VM capacity may not be available in every region, and Oracle may reclaim idle instances under its policies.
- Account signup may require a payment card for verification. Ask a parent/guardian to handle signup and payment details if needed. Do not share card details or bot secrets in GitHub.
- If you already use a persistent PostgreSQL database, set `DATABASE_URL` in `.env`. Otherwise the bot uses SQLite at `DATABASE_PATH`; on a persistent VM, that file stays on the VM, but make backups.
- Leave `PUBLIC_URL` empty to use polling. Do not run polling and webhook mode at the same time.

## 1. Create the VM

Create an Ubuntu LTS VM in Oracle Cloud and use an SSH key. For a small bot, start with the smallest Always Free VM that can be provisioned. Do not select paid resources.

## 2. Install the app

SSH into the VM, then run:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip fonts-dejavu-core
sudo useradd --system --create-home --home-dir /opt/diamond-bot --shell /usr/sbin/nologin diamondbot
sudo mkdir -p /opt/diamond-bot
sudo chown -R diamondbot:diamondbot /opt/diamond-bot
sudo -u diamondbot git clone https://github.com/Raonsonapp/Diamond-bot.git /opt/diamond-bot
cd /opt/diamond-bot
sudo -u diamondbot python3 -m venv .venv
sudo -u diamondbot .venv/bin/pip install --upgrade pip
sudo -u diamondbot .venv/bin/pip install -r requirements.txt
sudo -u diamondbot cp .env.example .env
sudo nano /opt/diamond-bot/.env
```

Fill in at least `BOT_TOKEN`, `ADMIN_CHAT_ID`, and `ADMIN_USER_IDS`. Keep `PUBLIC_URL=` empty for polling. Never paste real tokens into a public issue or commit them to GitHub.

## 3. Start as a background service

Copy the example service file into systemd:

```bash
sudo cp /opt/diamond-bot/deploy/oracle/diamond-bot.service.example /etc/systemd/system/diamond-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now diamond-bot
sudo systemctl status diamond-bot --no-pager
```

View logs:

```bash
sudo journalctl -u diamond-bot -f
```

After a new GitHub update:

```bash
sudo systemctl stop diamond-bot
sudo -u diamondbot git -C /opt/diamond-bot pull
sudo -u diamondbot /opt/diamond-bot/.venv/bin/pip install -r /opt/diamond-bot/requirements.txt
sudo systemctl start diamond-bot
```

## 4. Database safety

For important orders and balances, prefer PostgreSQL (for example, a properly configured Supabase project) and set `DATABASE_URL` in `.env`. If using SQLite, back up `/opt/diamond-bot/diamond_bot.db` regularly. Do not delete the database during an update.

## Troubleshooting

- `sudo journalctl -u diamond-bot -n 100 --no-pager` shows recent logs.
- Check `.env` for missing variables and ensure there is only one active bot process polling with the same token.
- If Oracle cannot allocate a free VM, try another availability domain in the selected home region or wait; do not accidentally provision a paid instance.
