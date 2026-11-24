FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV NEW_RELIC_APP_NAME="blacklist-api-dev" \
    NEW_RELIC_LOG=stdout \
    NEW_RELIC_LOG_LEVEL=info \
    NEW_RELIC_DISTRIBUTED_TRACING_ENABLED=true

EXPOSE 8000

ENTRYPOINT ["newrelic-admin", "run-program"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "application:application"]
