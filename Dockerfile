FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY enauczanie_mcp ./enauczanie_mcp
RUN pip install --no-cache-dir .

ENV ENAUCZANIE_DOWNLOAD_DIR=/downloads
ENTRYPOINT ["python", "-m", "enauczanie_mcp"]
