FROM python:3.11-slim
RUN apt-get update && apt-get install -y g++ libffi-dev libssl-dev python3-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium && playwright install-deps
COPY . .
CMD ["python", "main.py"]
