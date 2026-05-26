FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    g++ \
    libffi-dev \
    libssl-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# כאן אנחנו מתקינים את הדפדפן בזמן ה-Build
RUN playwright install chromium && playwright install-deps chromium

CMD ["python", "main.py"]
