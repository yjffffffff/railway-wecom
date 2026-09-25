# 多阶段构建：精简最终镜像
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

# 非root用户
RUN groupadd -r appuser && useradd -r -g appuser appuser

# 复制依赖
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# 复制代码
COPY --chown=appuser:appuser . .

# 创建必要目录
RUN mkdir -p /app/data /app/logs && chown -R appuser:appuser /app

USER appuser

# 健康检查（可选）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# 入口：单次运行，适配 Railway Cron
ENTRYPOINT ["python", "main.py"]
CMD ["--mode", "scan"]