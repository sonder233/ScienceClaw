# Local 模式去 Sandbox 容器依赖设计

## 背景

ScienceClaw 的 local 模式（`STORAGE_BACKEND=local`）已通过 Repository 抽象层去除了 MongoDB 依赖，但仍依赖 sandbox Docker 容器提供代码执行、文件操作和 RPA 浏览器控制能力。目标是让 local 模式完全脱离容器，组件尽量精简，适合个人本地开发和使用。

## 设计目标

- `STORAGE_BACKEND=local` 时，backend 启动不依赖任何 Docker 容器
- 代码执行、文件操作能力完整保留，直接在宿主机执行（无隔离，个人本地场景）
- RPA 录制/回放保留，使用本地 Playwright + CDP screencast 双向交互替代 VNC
- 不新增配置项，复用 `STORAGE_BACKEND` 控制行为切换
- 上层代码（SSE 协议、session 管理、中间件）零改动

## 架构概览

```
STORAGE_BACKEND=mongo (默认，现有行为)
  存储: MongoDB (Motor)
  沙箱: FullSandboxBackend → HTTP REST → sandbox 容器
  RPA:  CDP → sandbox 容器浏览器 → noVNC 前端展示

STORAGE_BACKEND=local (本设计)
  存储: FileRepository (本地 JSON 文件)
  沙箱: LocalShellBackend → subprocess + 本地文件系统
  RPA:  LocalCDPConnector → 本地 Playwright → CDP screencast 前端展示
```

## 模块一：沙箱后端切换

### 方案

复用 deepagents 库内置的 `LocalShellBackend`（继承自 `FilesystemBackend`，实现 `SandboxBackendProtocol`），替代自定义的 `FullSandboxBackend`。

### LocalShellBackend 能力映射

| Protocol 方法 | FullSandboxBackend (远程) | LocalShellBackend (本地) |
|---------------|--------------------------|-------------------------|
| `execute()` | HTTP POST → `/v1/shell/exec` | `subprocess.run(shell=True)` |
| `read()` | HTTP → `/v1/file/read` | 本地 `open()` 读文件 |
| `write()` | HTTP → `/v1/file/write` | 本地 `open()` 写文件 |
| `edit()` | HTTP → `/v1/file/str_replace_editor` | 字符串替换 + 写回 |
| `ls_info()` | HTTP → `/v1/file/list` | `os.scandir()` |
| `glob_info()` | HTTP → `/v1/file/find` | `pathlib.Path.glob()` |
| `grep_raw()` | HTTP → `/v1/file/search` | ripgrep 或 Python fallback |
| `upload_files()` | HTTP multipart 上传 | 本地文件写入 |
| `download_files()` | HTTP 下载 | 本地文件读取 |
| `get_context()` | HTTP → `/v1/sandbox` | 返回本地环境信息 |

### 改动点：`agent.py` 的 `_build_backend()`

```python
def _build_backend(session_id, user_id, ...):
    if settings.storage_backend == "local":
        from deepagents.backends import LocalShellBackend, FilesystemBackend
        workspace = f"{_WORKSPACE_DIR}/{session_id}"
        os.makedirs(workspace, exist_ok=True)
        sandbox = LocalShellBackend(
            root_dir=workspace,
            timeout=ts.sandbox_exec_timeout,
            inherit_env=True,
        )
        return CompositeBackend(
            default=sandbox,
            routes={
                "/builtin-skills/": FilesystemBackend(root_dir=_BUILTIN_SKILLS_DIR),
                "/skills/": FilesystemBackend(root_dir=settings.external_skills_dir),
            },
        )
    else:
        # 现有逻辑不变
        sandbox = FullSandboxBackend(...)
        return CompositeBackend(
            default=sandbox,
            routes={
                "/builtin-skills/": FilesystemBackend(...),
                "/skills/": MongoSkillBackend(...),
            },
        )
```

### 工具调用链路（无需改动）

LLM 调用的工具名是 `execute`（由 deepagents `FilesystemMiddleware` 创建），不是 `sandbox_execute_bash`。调用链：

```
LLM → execute(command) → FilesystemMiddleware._get_backend() → backend.execute()
```

`sse_protocol.py` 注册的 `sandbox_execute_bash` 等名字仅用于前端 UI 元数据（图标、标签），不参与工具路由。切换 backend 后整条链路自动适配。

### Skills 注入

local 模式下跳过 `_inject_skills_to_sandbox()`。skill 文件已在宿主机文件系统上，`LocalShellBackend` 直接读取。`CompositeBackend` 将 `/builtin-skills/` 和 `/skills/` 路由到 `FilesystemBackend`。

### 工作目录

保持按 session 隔离：`{WORKSPACE_DIR}/{session_id}/`。`LocalShellBackend` 的 `root_dir` 设为该路径。

## 模块二：RPA 本地化

### 现有架构（云端）

sandbox 容器内 Playwright headless=False → Xvfb 虚拟显示 → x11vnc → noVNC 前端展示 + 交互。

### 本地架构

宿主机 Playwright headful → CDP screencast 画面推送 → CDP Input.dispatch* 输入注入。

### 组件设计

#### 2.1 LocalCDPConnector

改动文件：`rpa/cdp_connector.py`

职责：在宿主机启动 Playwright 浏览器（headful 模式），获取 CDP WebSocket endpoint。

```python
class LocalCDPConnector:
    async def launch(self) -> tuple[Browser, CDPSession]:
        """启动本地 Playwright 浏览器，返回 browser 实例和 CDP session"""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=False)
        context = self._browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        # 通过 Playwright 的 CDP session API 获取底层 CDP 连接
        self._cdp_session = await page.context.new_cdp_session(page)
        return self._browser, self._cdp_session

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
```

`rpa/manager.py` 的 `create_session()` 根据 `settings.storage_backend` 选择 `LocalCDPConnector` 或现有的远程 `CDPConnector`。

#### 2.2 CDP Screencast 服务

新增文件：`rpa/screencast.py`

职责：通过 CDP 协议获取浏览器画面帧，推送到前端；接收前端输入事件，注入到浏览器。

画面推送（浏览器 → 前端）：
- 通过 CDP `Page.startScreencast` 启动持续截屏
- 监听 `Page.screencastFrame` 事件，获取 base64 编码的图片帧
- 通过 WebSocket 推送帧数据到前端
- 每帧需调用 `Page.screencastFrameAck` 确认，控制帧率

输入注入（前端 → 浏览器）：
- 前端 canvas 捕获鼠标/键盘事件，通过同一 WebSocket 上行发送
- 后端收到后通过 CDP 注入：
  - `Input.dispatchMouseEvent` — mousePressed/mouseReleased/mouseMoved/mouseWheel
  - `Input.dispatchKeyEvent` — keyDown/keyUp/char
- 坐标映射：前端发送归一化坐标（0.0-1.0），后端按 screencast 元数据中的实际视口尺寸换算为像素坐标

```python
class ScreencastService:
    def __init__(self, cdp_session):
        self._cdp = cdp_session
        self._viewport = {"width": 1280, "height": 720}

    async def start(self, websocket):
        """启动 screencast 并绑定 WebSocket 双向通信"""
        # 启动画面推送
        await self._cdp.send("Page.startScreencast", {
            "format": "jpeg",
            "quality": 60,
            "maxWidth": 1280,
            "maxHeight": 720,
        })

        # 双向消息循环
        async for message in websocket:
            event = json.loads(message)
            if event["type"] == "mouse":
                await self._dispatch_mouse(event)
            elif event["type"] == "keyboard":
                await self._dispatch_key(event)

    async def _dispatch_mouse(self, event):
        x = event["x"] * self._viewport["width"]
        y = event["y"] * self._viewport["height"]
        await self._cdp.send("Input.dispatchMouseEvent", {
            "type": event["action"],  # mousePressed/mouseReleased/mouseMoved
            "x": x, "y": y,
            "button": event.get("button", "left"),
            "clickCount": event.get("clickCount", 1),
        })

    async def _dispatch_key(self, event):
        await self._cdp.send("Input.dispatchKeyEvent", {
            "type": event["action"],  # keyDown/keyUp/char
            "key": event.get("key", ""),
            "code": event.get("code", ""),
            "text": event.get("text", ""),
        })

    async def stop(self):
        await self._cdp.send("Page.stopScreencast")
```

#### 2.3 WebSocket Endpoint

改动文件：`route/rpa.py`

新增 `/api/v1/rpa/screencast` WebSocket endpoint：

```python
@router.websocket("/screencast/{session_id}")
async def rpa_screencast(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = rpa_manager.get_session(session_id)
    screencast = ScreencastService(session.cdp_session)

    # CDP screencastFrame 事件 → WebSocket 推送
    async def on_frame(frame_data):
        await websocket.send_json({
            "type": "frame",
            "data": frame_data["data"],  # base64 jpeg
            "metadata": frame_data["metadata"],
        })

    session.cdp_session.on("Page.screencastFrame", on_frame)
    await screencast.start(websocket)
```

#### 2.4 前端改动

改动文件：前端 `rpa/recorder` 页面

local 模式下替换 noVNC iframe 为 canvas + WebSocket 客户端：

- 连接 `ws://localhost:12001/api/v1/rpa/screencast/{sessionId}`
- 收到 `frame` 消息时，将 base64 图片绘制到 canvas
- canvas 监听 `mousedown/mouseup/mousemove/keydown/keyup` 事件
- 将事件归一化后通过 WebSocket 发送（坐标除以 canvas 尺寸得到 0-1 范围）
- 根据 `STORAGE_BACKEND` 配置（可通过现有 API 获取）决定显示 noVNC iframe 还是 canvas

### RPA 事件捕获

现有的 JS 事件捕获逻辑（`page.expose_function` + 注入脚本）不变。录制时：
- 云端：用户通过 noVNC 操作 → sandbox 浏览器捕获事件
- 本地：用户通过 CDP 输入注入操作 → 本地浏览器捕获事件

事件格式和后续的 locator 生成、脚本生成逻辑完全一致。

### RPA 脚本执行

现有 `executor.py` 通过 `sandbox_execute_bash` + nohup 在 sandbox 里跑 Playwright 脚本。

local 模式下：`LocalShellBackend.execute()` 直接在宿主机用 subprocess 执行，不需要 nohup + sentinel 文件轮询模式（因为不存在 MCP 调用返回即杀进程的问题）。`executor.py` 需要根据 `storage_backend` 选择执行方式：
- `mongo`：现有 nohup + sentinel 模式
- `local`：直接 subprocess 执行，等待完成

## 模块三：不改动的部分

以下模块通过 Protocol 抽象层自动适配，无需任何改动：

| 模块 | 文件 | 原因 |
|------|------|------|
| SSE 协议 | `sse_protocol.py` | 工具元数据仅用于 UI 展示 |
| SSE 中间件 | `sse_middleware.py` | 只包装工具调用，不关心底层实现 |
| 输出落盘 | `offload_middleware.py` | 只看工具名和输出大小 |
| Session 管理 | `sessions.py` | 工具名映射仅用于前端展示 |
| 任务设置 | `task_settings.py` | timeout 等参数通过构造函数传入 backend |
| Chat 路由 | `chat.py` | 只调用 agent，不直接接触 sandbox |
| Task Service | `task-service/` | 独立服务，通过 API 调用 chat |

## 改动文件清单

| 文件 | 改动类型 | 复杂度 |
|------|---------|--------|
| `backend/deepagent/agent.py` | 修改 `_build_backend()` + 跳过 skill 注入 | 低 |
| `backend/rpa/cdp_connector.py` | 新增 `LocalCDPConnector` 类 | 中 |
| `backend/rpa/screencast.py` | 新增 CDP screencast + 输入注入服务 | 高 |
| `backend/rpa/manager.py` | 根据 storage_backend 选择连接器 | 低 |
| `backend/rpa/executor.py` | local 模式直接 subprocess 执行 | 低 |
| `backend/route/rpa.py` | 新增 WebSocket endpoint | 中 |
| `frontend/src/pages/rpa/recorder` | noVNC → canvas + WebSocket 双向通信 | 中 |

## 新增依赖

- 宿主机需要 `playwright install chromium`（仅 RPA 功能需要）
- 无其他新增依赖（`LocalShellBackend` 已在 deepagents 库中）

## 已知限制

- 本地模式无安全隔离，代码直接在宿主机执行（个人本地使用场景可接受）
- CDP screencast 帧率有限（10-30fps），交互体感略逊于 VNC
- 文件上传对话框、系统级弹窗等 CDP 无法拦截的场景，RPA 录制可能受限
- `find-skills` builtin skill 依赖 sandbox 预装的 `skills` CLI，local 模式需用户自行安装
