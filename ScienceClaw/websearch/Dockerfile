FROM python:3.12-slim

WORKDIR /search

ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Replace Debian sources with Aliyun mirrors
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

# Install Python dependencies
RUN pip install --no-cache-dir \
    "crawl4ai" \
    fastapi \
    "uvicorn[standard]" \
    gunicorn \
    aiohttp \
    pydantic \
    python-dotenv \
    markdown \
    beautifulsoup4

# Install Playwright Chromium browser + system deps
RUN python -m playwright install --with-deps chromium

# Copy websearch source code
COPY . /search/

EXPOSE 8068

CMD ["gunicorn", "main:app", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8068", "--timeout", "120"]
