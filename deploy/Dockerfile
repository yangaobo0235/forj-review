FROM python:3.11-slim-bookworm AS builder

ARG UV_VERSION=0.12.11

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY review-pages ./review-pages
RUN uv sync --frozen --no-dev


FROM python:3.11-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tzdata 保留：compose 里的 TZ=Asia/Shanghai 只是环境变量，C 库 localtime 需要
# /usr/share/zoneinfo 下的时区数据文件才能生效；缺了它日志时间戳仍回落 UTC，
# 按天轮转的切点也会偏 8 小时。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 review-agent \
    && useradd --uid 10001 --gid review-agent --home-dir /app --no-create-home review-agent

WORKDIR /app

COPY --from=builder --chown=review-agent:review-agent /app /app

USER review-agent

ARG RELEASE_ID=development
ENV REVIEW_RELEASE_ID=${RELEASE_ID}

EXPOSE 8010

CMD ["uvicorn", "forj_review_assistant.main:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
