FROM python:3.12-slim

# curl-cffi needs these for its compiled curl binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcurl4 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

ENV DB_PATH=/app/data/tesla_inventory.db
EXPOSE 8080

CMD ["python", "main.py"]
