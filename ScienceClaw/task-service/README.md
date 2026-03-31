# Task Scheduler Service

独立部署的任务调度服务，与主聊天服务通过 API 交互。支持自然语言定时规则、asyncio 内置调度、调用聊天接口执行 LLM 任务、多平台 Webhook 推送与执行历史。

## 功能

- **任务管理**: 创建 / 列表 / 更新 / 删除任务
- **定时规则**: 自然语言描述（如「每天早上9点」）经 LLM 转为 crontab
- **内置调度**: asyncio 后台循环每分钟检查到期任务并并发执行（无需 Redis/Celery）
- **执行流程**: 调用主服务 `POST /api/v1/chat` 执行任务，记录 `task_runs`，完成后推送 Webhook

## 环境变量

| 变量 | 说明 |
|------|------|
| MONGODB_HOST / MONGODB_PORT / MONGODB_DB / MONGODB_USER / MONGODB_PASSWORD | MongoDB 连接 |
| CHAT_SERVICE_URL | 主聊天服务地址（如 `http://backend:8000`） |
| CHAT_SERVICE_API_KEY | 调用主服务 `/api/v1/chat` 时使用的 API Key（需与主服务 TASK_SERVICE_API_KEY 一致） |
| DS_API_KEY / DS_URL / DS_MODEL | 自然语言转 crontab 时使用的 LLM |

## API

- `POST /tasks` — 创建任务
- `GET /tasks` — 任务列表
- `GET /tasks/{id}` — 任务详情
- `PUT /tasks/{id}` — 更新任务
- `DELETE /tasks/{id}` — 删除任务
- `GET /tasks/{id}/runs` — 执行历史

## 本地运行

```bash
cd ScienceClaw/task-service
pip install -r requirements.txt
# 设置环境变量后：
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## Docker Compose

与主项目一起编排时，只需启动 `scheduler_api` 单个容器。主服务需配置 `TASK_SERVICE_API_KEY`，任务服务配置相同值到 `CHAT_SERVICE_API_KEY` 以调用主服务聊天接口。
