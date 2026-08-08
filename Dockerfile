FROM python:3.12-slim

WORKDIR /app

# fonts-dejavu-core provides the Cyrillic-capable TTF fonts the receipt
# image generator needs (bot/services/receipt_image.py) — python:3.12-slim
# ships with no fonts at all otherwise.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
