FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create a non-root user (UID 1000 to match host ubuntu user for volume mounts)
RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser -m appuser \
    && mkdir -p data logs \
    && chown -R appuser:appuser /app

# Copy only the application source, owned by appuser
COPY --chown=appuser:appuser src/ ./src/

USER appuser

ENV PYTHONUNBUFFERED=1

CMD ["python", "src/bot.py"]
