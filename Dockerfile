# Force rebuild v2
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Override the default entrypoint
ENTRYPOINT []
CMD ["python", "src/bot.py"]
