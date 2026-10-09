FROM python:3.11-slim

# 安装git（调度器需要git commit/push存档到GitHub）
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# 启动调度器（常驻进程）
CMD ["python", "scheduler.py"]