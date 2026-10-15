FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 app && useradd -u 10001 -g app -m -s /usr/sbin/nologin app

# Codex CLI (fase 6: gerar imagens com a subscrição ChatGPT). Versão fixa para não partir sem aviso.
ARG CODEX_VERSION=0.158.0
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap && rm -rf /var/lib/apt/lists/* \
    && for bin in codex codex-code-mode-host; do \
         curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/${bin}-x86_64-unknown-linux-musl.tar.gz" \
           | tar -xz -C /usr/local/bin \
         && mv "/usr/local/bin/${bin}-x86_64-unknown-linux-musl" "/usr/local/bin/${bin}"; \
       done \
    && codex --version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium sem ecrã (Playwright): o conector MCP tira imagens das maquetes 3D com o mesmo visualizador three.js da app.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN pip install --no-cache-dir playwright==1.63.0 \
    && playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /opt/playwright

# Claude Code CLI (Assistente, fase 5), instalado para o utilizador sem privilégios.
USER app
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && /home/app/.local/bin/claude --version
ENV PATH="/home/app/.local/bin:${PATH}" \
    DISABLE_AUTOUPDATER=1

USER root
COPY . .
USER app
EXPOSE 8000
CMD ["sh", "/app/docker-entrypoint.sh"]
