#FROM ghcr.io/agent-infra/sandbox:latest
FROM enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest

# ── 国内镜像源配置 ──────────────────────────────────────────────────────
ARG PIP_MIRROR=https://mirrors.aliyun.com/pypi/simple/
ARG NPM_MIRROR=https://registry.npmmirror.com
ARG APT_MIRROR=mirrors.aliyun.com

# ── 0. 统一 Python 环境为 3.12 ─────────────────────────────────────
ENV PATH="/opt/python3.12/bin:${PATH}"
RUN ln -sf /opt/python3.12/bin/python3.12 /usr/local/bin/python3 \
    && ln -sf /opt/python3.12/bin/python3.12 /usr/local/bin/python \
    && ln -sf /opt/python3.12/bin/pip3 /usr/local/bin/pip3 \
    && ln -sf /opt/python3.12/bin/pip3 /usr/local/bin/pip

# ── 1. 复制依赖文件 ──────────────────────────────
COPY ScienceClaw/sandbox/requirements.txt /tmp/requirements.txt

# ── 2. 一次性安装所有依赖（系统 + Node + Python）──────────────────────
RUN sed -i "s@http://.*archive.ubuntu.com@http://${APT_MIRROR}@g" /etc/apt/sources.list 2>/dev/null || true \
    && sed -i "s@http://.*security.ubuntu.com@http://${APT_MIRROR}@g" /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential clang \
        fonts-wqy-microhei fontconfig \
        poppler-utils pandoc libreoffice-writer \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/* \
    && npm config set registry ${NPM_MIRROR} \
    && npm install -g skills docx pptxgenjs \
    && npm cache clean --force \
    && pip config set global.timeout 600 \
    && pip config set global.trusted-host "mirrors.aliyun.com files.pythonhosted.org" \
    && python3 -m pip install --no-cache-dir -r /tmp/requirements.txt -i ${PIP_MIRROR} \
    && rm -rf /root/.cache/pip /tmp/requirements.txt \
    && python3 -c "import matplotlib; matplotlib.get_cachedir()" \
    && rm -rf /root/.cache/matplotlib /tmp/matplotlib* \
    && python3 -c "import matplotlib.font_manager; matplotlib.font_manager._load_fontmanager(try_read_cache=False)"

ENV CC=clang NODE_PATH="/usr/lib/node_modules"

# ── 3. 项目文件 ────────────────────────────────────────────────────────
COPY ScienceClaw/websearch/seekr_sdk.py /opt/python3.12/lib/python3.12/site-packages/seekr_sdk.py
COPY ScienceClaw/sandbox/tool_runner.py /app/_tool_runner.py
