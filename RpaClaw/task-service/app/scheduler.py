"""
Asyncio-based task scheduler — replaces Celery beat + worker.

Runs inside the FastAPI process: a background loop checks MongoDB every 60s
for tasks whose crontab matches the current minute, then executes them
concurrently via asyncio tasks.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import asyncio
import httpx
from croniter import croniter
from loguru import logger

from app.core.config import settings
from app.core.db import db
from app.services.chat_client import run_task_chat
from app.services.feishu import notify_task_failed, notify_task_success, notify_task_started
from app.services.webhook_sender import send_webhook


def _display_tz() -> ZoneInfo:
    tz_name = (settings.display_timezone or "").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def _fmt_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    tz = _display_tz()
    if hasattr(dt, "astimezone"):
        dt = dt.astimezone(tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class TaskScheduler:
    """Single-process asyncio scheduler for cron-based tasks."""

    def __init__(self, interval: float = 60.0):
        self._interval = interval
        self._task: Optional[asyncio.Task] = None
        self._running_tasks: set[str] = set()

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("TaskScheduler started (interval={}s)", self._interval)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
            logger.info("TaskScheduler stopped")

    async def _loop(self) -> None:
        # Wait a few seconds on startup so MongoDB is ready
        await asyncio.sleep(3)
        while True:
            try:
                await self._check_due_tasks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler loop error")
            await asyncio.sleep(self._interval)

    async def _check_due_tasks(self) -> None:
        now = datetime.now(_display_tz()).replace(second=0, microsecond=0)
        cursor = db.get_collection("tasks").find({"status": "enabled"})
        async for doc in cursor:
            crontab_str = doc.get("crontab") or ""
            if not crontab_str:
                continue
            task_id = str(doc["_id"])
            try:
                if croniter.match(crontab_str, now):
                    if task_id in self._running_tasks:
                        logger.debug("Task {} still running, skip", task_id)
                        continue
                    asyncio.create_task(self._run_task(task_id))
                    logger.info("Dispatched task {}", task_id)
            except Exception as e:
                logger.warning("Crontab check failed for {}: {}", task_id, e)

    async def _run_task(self, task_id: str) -> None:
        self._running_tasks.add(task_id)
        try:
            await self._execute_task(task_id)
        finally:
            self._running_tasks.discard(task_id)

    async def _execute_task(self, task_id: str) -> None:
        doc = await db.get_collection("tasks").find_one({"_id": task_id})
        if not doc:
            logger.warning("run_task: task {} not found", task_id)
            return

        name = doc.get("name", "未命名")
        prompt = doc.get("prompt", "")
        webhook = doc.get("webhook") or ""
        webhook_ids = doc.get("webhook_ids") or []
        event_config = doc.get("event_config") or []
        model_config_id = (doc.get("model_config_id") or "").strip() or None
        notify_start = "notify_on_start" in event_config
        user_id = (doc.get("user_id") or "").strip() or None

        start_time = datetime.now(timezone.utc)
        runs_coll = db.get_collection("task_runs")

        run_doc: Dict[str, Any] = {
            "task_id": task_id,
            "status": "running",
            "chat_id": None,
            "start_time": start_time,
            "end_time": None,
            "result": None,
            "error": None,
        }
        ins = await runs_coll.insert_one(run_doc)
        run_id = ins.inserted_id

        try:
            if notify_start:
                await self._notify_start(webhook, webhook_ids, name, start_time)

            result = await run_task_chat(
                task_id, prompt, user_id=user_id, model_config_id=model_config_id
            )
            end_time = datetime.now(timezone.utc)

            if "error" in result:
                await runs_coll.update_one(
                    {"_id": run_id},
                    {"$set": {"status": "failed", "end_time": end_time, "error": result["error"]}},
                )
                await self._notify_finish(webhook, webhook_ids, name, start_time, end_time, False, result["error"])
                return

            await runs_coll.update_one(
                {"_id": run_id},
                {"$set": {
                    "status": "success",
                    "chat_id": result.get("chat_id"),
                    "end_time": end_time,
                    "result": result.get("output", ""),
                    "error": None,
                }},
            )
            await self._notify_finish(webhook, webhook_ids, name, start_time, end_time, True, result.get("output", ""))

        except Exception as e:
            logger.exception("run_task failed for {}", task_id)
            end_time = datetime.now(timezone.utc)
            await runs_coll.update_one(
                {"_id": run_id},
                {"$set": {"status": "failed", "end_time": end_time, "error": str(e)}},
            )
            await self._notify_finish(webhook, webhook_ids, name, start_time, end_time, False, str(e))

    # ── Notification helpers ──

    async def _notify_start(
        self, webhook: str, webhook_ids: list, task_name: str, start_time: datetime
    ) -> None:
        start_str = _fmt_time(start_time)
        if webhook and webhook.strip():
            await notify_task_started(webhook, task_name, start_str)
        for wid in webhook_ids:
            try:
                wh_doc = await db.get_collection("webhooks").find_one({"_id": wid})
                if not wh_doc:
                    continue
                title = f"🚀 任务开始执行：{task_name}"
                content = f"**⏱ 开始时间**\n{start_str}"
                await send_webhook(wh_doc.get("type", "feishu"), wh_doc.get("url", ""), title, content)
            except Exception as e:
                logger.warning("Failed to notify start webhook {}: {}", wid, e)

    async def _notify_finish(
        self, webhook: str, webhook_ids: list, task_name: str,
        start_time: datetime, end_time: datetime, success: bool, result_or_error: str,
    ) -> None:
        start_str = _fmt_time(start_time)
        end_str = _fmt_time(end_time)
        # Legacy single webhook (Feishu)
        if webhook and webhook.strip():
            if success:
                await notify_task_success(webhook, task_name, start_str, end_str, result_or_error)
            else:
                await notify_task_failed(webhook, task_name, start_str, end_str, result_or_error)
        # Managed webhooks
        if not webhook_ids:
            return
        truncated = result_or_error[:500] + "..." if len(result_or_error) > 500 else result_or_error
        label = "执行结果" if success else "错误信息"
        title = f"{'✅ 任务执行成功' if success else '❌ 任务执行失败'}：{task_name}"
        content = f"**⏱ 开始时间**\n{start_str}\n\n**⏱ 结束时间**\n{end_str}\n\n**📋 {label}**\n\n{truncated}"
        for wid in webhook_ids:
            try:
                wh_doc = await db.get_collection("webhooks").find_one({"_id": wid})
                if not wh_doc:
                    continue
                await send_webhook(wh_doc.get("type", "feishu"), wh_doc.get("url", ""), title, content)
            except Exception as e:
                logger.warning("Failed to notify webhook {}: {}", wid, e)


scheduler = TaskScheduler()
