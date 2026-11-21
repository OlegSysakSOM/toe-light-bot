FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Kyiv

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Стан (id повідомлення, останній графік) лежить у /app/data — підключіть сюди том,
# щоб він переживав перезапуски й оновлення.
ENV STATE_FILE=data/state.json \
    LOG_FILE=

CMD ["python", "bot.py"]
