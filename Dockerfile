# השתמש בגרסה מלאה יותר של פייתון כדי שיהיו כל הכלים הדרושים
FROM python:3.11

# התקנת כל תלויות המערכת ש-Playwright צריך
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# עדכון pip והתקנת הדרישות
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# התקנת דפדפן Playwright ישירות
RUN playwright install chromium && playwright install-deps

COPY . .

CMD ["python", "main.py"]
