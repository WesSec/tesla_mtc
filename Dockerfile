FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Amsterdam

# poppler-utils converts Tesla's invoice PDFs; tzdata gives the web runner local times
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 tesla_mtc
USER tesla_mtc

EXPOSE 8080
# Web interface by default; run once without it with: docker compose run --rm tesla_mtc python main.py
CMD ["python", "-u", "web/runner.py"]
