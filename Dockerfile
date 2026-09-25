FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY main.py .
COPY samples/ ./samples/
COPY .env.example ./.env.example

RUN mkdir -p output

ENTRYPOINT ["python", "main.py"]
CMD ["--gl-path", "samples/gl_sample.csv", "--lhdn-path", "samples/lhdn_sample.json", "--output", "output/reconciliation_summary.xlsx"]
