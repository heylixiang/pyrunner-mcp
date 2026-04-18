# PyRunner MCP

[English README](./README.md)

PyRunner MCP 是一个面向 AI Agent 的自托管 Python 执行栈，主要由以下几部分组成：

- 受限的 Python AST 解释器
- 带有超时终止与恢复能力的 worker 进程运行层
- 高层会话服务
- 支持 `stdio` 和 Streamable HTTP 的轻量级 MCP 服务框架

这个项目适合那些希望为 Agent 提供 Python 执行能力，但又不想依赖外部沙箱产品或第三方 MCP 框架的团队。

## 为什么做这个项目

很多面向 Agent 的 Python 执行服务，本质上只是对 `exec`、容器运行时，或者现成 MCP SDK 的一层薄封装。这个项目走的是另一条路线：

- 不直接调用原始 `exec`，而是自己实现受限 Python 解释器
- Python 执行不放在 MCP 主进程内，而是放到独立 worker 进程
- 提供清晰的 service 层作为集成入口，而不是把业务逻辑直接绑死在 transport 上
- 自己实现 MCP 服务框架，包括 JSON-RPC 处理、tool 注册、stdio transport 和 Streamable HTTP transport
- 支持把宿主侧的受控 helper function 注入到 Python 沙箱中，并通过 MCP resource 暴露 stub 给 Agent

这样做的好处是：系统结构更可控，更容易研究、扩展，也更容易在后续逐步加强隔离能力。

## 核心特性

- 基于 AST 解释器的受限 Python 执行
- 基于 session 的执行模型，可跨多次调用保留状态
- 支持在执行代码中使用 top-level `await`
- 返回结构化执行结果，包括 output、logs、timeout 状态和错误信息
- 宿主侧响应超时控制，可终止卡死 worker 并自动恢复
- 在未指定自定义 workdir 时，为每个 session 创建独立临时工作目录
- 支持向沙箱注入受控的 Python helper function
- 可通过 MCP resource（例如 `api://functions`）让 Agent 发现 helper API
- 自实现 MCP 框架，支持装饰器式注册
- 支持 `stdio` 和 `streamable-http` 两种 transport
- 支持本地开发时的自动重载

## 项目做了什么

PyRunner MCP 允许 MCP 客户端通过 `execute_python_code` 这样的 tool 提交 Python 代码，在受限解释器中执行，借助 session 跨调用保留状态，并以结构化结果返回执行输出。

当前仓库包含以下核心模块：

- `lib.python_executor`：基于 AST 的受限 Python 执行
- `lib.sandbox_runner`：worker 进程隔离、请求响应传输、超时终止、重启恢复、按 session 的工作目录管理
- `lib.python_service`：面向程序集成的高层 Python 执行会话 API
- `lib.mcp_server`：通用 MCP 框架，支持 tools、resources、prompts、stdio transport 和 Streamable HTTP transport
- `lib.sandbox_api`：用于注册宿主 helper function 并生成 Python 风格 stub 的小型组件
- `src/apps/features`：注入数据库 helper function 的 MCP app 示例
- `src/apps/browser`：注入 Playwright 浏览器 helper function 的 MCP app 示例

## 架构

整个系统是分层设计的：

1. `lib.python_executor`
   受限 Python 解释器，负责 AST 求值、import 白名单、受控 builtins、print 捕获、执行状态和执行限制。

2. `lib.sandbox_runner`
   executor 外层的进程边界。负责启动 worker 进程、通过 stdio 发送请求、执行宿主侧响应超时控制、杀掉卡住的 worker，并在必要时重建。

3. `lib.python_service`
   高层 API，负责创建 session、执行代码、重置状态、列出 session、关闭 session。

4. `lib.mcp_server`
   通用 MCP 框架，负责 initialize 流程、协议校验、tool/resource/prompt 注册、stdio transport、Streamable HTTP transport 和运行时参数。

5. `src/apps/*`
   面向具体场景的 MCP 入口，将上述通用能力装配成可运行的 MCP 服务。

## 安全模型

这个项目提升了控制能力和隔离能力，但目前还不是一个真正的 OS 级沙箱。

### 当前已经实现

- 使用受限 AST 求值，而不是直接 `exec`
- import 白名单控制
- 危险函数检测
- dunder 属性访问限制
- print 捕获
- 解释器内部执行限制
- 独立 worker 进程执行
- worker 崩溃后的重启恢复
- 宿主侧响应超时终止与恢复
- 按 session 隔离的工作目录

### 当前还没有实现

- 只读文件系统挂载
- 真正的文件系统沙箱
- 网络隔离
- CPU 限制
- 内存限制
- PID / 进程数限制
- seccomp / AppArmor / gVisor / nsjail / cgroup 等隔离

### 实际上应如何理解

这个项目可以理解为：

- 比直接暴露 `exec` 更安全
- 比所有逻辑都跑在同一个进程里更可控
- 但还不能等价看作一个强化过的容器沙箱

如果你的目标是抵御恶意代码并获得强隔离保证，仍然需要在这个栈之下叠加真正的 OS 级沙箱能力。

## 仓库结构

```text
src/
  apps/
    browser/         # 带 Playwright helper 的 MCP app
    features/        # 带数据库 helper 的 MCP app
  core/              # 共享运行时配置
  lib/
    mcp_server/      # 通用 MCP 框架
    python_executor/ # 受限 AST 解释器
    python_service/  # 高层会话 API
    sandbox_api/     # helper function 注册与 stub 生成
    sandbox_runner/  # worker 进程管理
  tests/
    functions/
    mcp_server/
    python_executor/
    python_service/
    sandbox_runner/
```

## 安装

项目使用 `uv`。

### 基础安装

```bash
uv sync
```

### 安装开发依赖

```bash
uv sync --group dev
```

### 安装数据库示例 app 所需依赖

```bash
uv sync --group dev --group features
```

### 安装浏览器示例 app 所需依赖

```bash
uv sync --group dev --group browser
```

## 运行测试

如果要跑完整测试集，建议先安装开发依赖和 `features` 依赖组：

```bash
uv sync --group dev --group features
```

在仓库根目录执行：

```bash
uv run pytest -q src/tests
```

也可以只跑部分测试：

```bash
uv run pytest -q src/tests/python_executor src/tests/sandbox_runner src/tests/python_service src/tests/mcp_server
```

## 运行 MCP Server

当前仓库没有单一固定的 `src/main.py` 入口，而是提供了位于 `src/apps/` 下的多个 app 入口。

### 示例：以 Streamable HTTP 方式运行数据库场景 app

```bash
uv run python src/apps/features/main.py
```

默认运行参数：

- transport: `streamable-http`
- host: `127.0.0.1`
- port: `8094`
- path: `/mcp`
- health check: `/healthz`

### 以 stdio 方式运行

```bash
PYRUNNER_MCP_TRANSPORT=stdio uv run python src/apps/features/main.py
```

### 示例：运行浏览器场景 app

```bash
uv run python src/apps/browser/main.py
```

这个 app 需要安装 `browser` 依赖组，并且需要能够连接到可用的 Chromium CDP 实例。

## 运行时配置

共享 MCP 运行时配置通过环境变量读取：

- `PYRUNNER_MCP_TRANSPORT`
- `PYRUNNER_MCP_HOST`
- `PYRUNNER_MCP_PORT`
- `PYRUNNER_MCP_PATH`
- `PYRUNNER_MCP_ALLOWED_ORIGINS`
- `PYRUNNER_RELOAD`

也支持以下别名：

- `MCP_TRANSPORT`
- `MCP_HOST`
- `MCP_PORT`
- `MCP_PATH`
- `MCP_ALLOWED_ORIGINS`

Python 执行相关配置：

- `PYRUNNER_AUTHORIZED_IMPORTS`
- `PYRUNNER_EXECUTOR_TIMEOUT_SECONDS`
- `PYRUNNER_SANDBOX_RESPONSE_TIMEOUT_SECONDS`

### App 级配置

Browser app：

- `BROWSER_HOST`
- `BROWSER_PORT`
- `BROWSER_TIMEOUT`
- `BROWSER_WS_URL`
- `BROWSER_HOST_HEADER`

Features app：

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`

## MCP 行为

当前自实现的 MCP 框架支持：

- `initialize`
- `ping`
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/templates/list`
- `resources/read`
- `prompts/list`
- `prompts/get`
- `notifications/initialized`
- `notifications/cancelled`

示例 app 当前暴露：

- tool: `execute_python_code`
- resource: `api://functions`

`api://functions` resource 会返回被注入到沙箱中的宿主 helper function 的 Python 风格 stub。

## Python 执行模型

`execute_python_code` 的行为如下：

- 如果未传入 `sessionId`，会自动创建新 session
- 重复使用 `sessionId` 时，Python 状态会被保留
- 可以通过 `variables` 在执行前向当前 session 注入宿主变量
- 可以通过 `responseTimeoutSeconds` 覆盖单次调用的宿主侧响应超时
- 支持 top-level `await`
- 返回结果中包含 output、logs、timeout 状态和结构化错误信息

典型返回结构如下：

```json
{
  "kind": "python_execution",
  "ok": true,
  "sessionId": "f1f5c0f6...",
  "result": {
    "sessionId": "f1f5c0f6...",
    "output": 42,
    "logs": "hello",
    "isFinalAnswer": false,
    "timedOut": false,
    "error": null
  }
}
```

## MCP 调用示例

### 1. 初始化

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {
      "name": "example-client",
      "version": "1.0"
    }
  }
}
```

### 2. 读取可用 helper function

调用 `resources/read` 读取 `api://functions`，让 Agent 先看到当前沙箱中有哪些 helper stub。

### 3. 执行 Python

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "execute_python_code",
    "arguments": {
      "code": "x = 10\nx + 32"
    }
  }
}
```

### 4. 复用返回的 session

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "execute_python_code",
    "arguments": {
      "sessionId": "returned-session-id",
      "code": "x * 2"
    }
  }
}
```

## 程序化使用

### 直接使用 PythonExecutionService

```python
from lib.python_service import PythonExecutionPolicy, PythonExecutionService

service = PythonExecutionService()

session = service.create_session(
    policy=PythonExecutionPolicy(
        additional_authorized_imports=["statistics"],
        sandbox_response_timeout_seconds=2.0,
    ),
    initial_variables={"numbers": [1, 2, 3]},
)

result = service.execute_code(
    session.session_id,
    "import statistics\nstatistics.mean(numbers)",
)

print(result.output)  # 2
service.close()
```

### 组装一个 MCP app

```python
from lib.mcp_server import MCPApp

mcp = MCPApp("Demo")

@mcp.tool
def ping() -> dict:
    return {"ok": True}

if __name__ == "__main__":
    raise SystemExit(mcp.run())
```

## 注入宿主 Helper Function

仓库中提供了 `lib.sandbox_api.SandboxAPI`，可以用来：

- 注册对 Agent 有用且受控的 Python callable
- 将这些 callable 注入解释器
- 生成 Python 风格的 stub 文本，并作为 MCP resource 暴露出去

示例 app 中的浏览器 helper 和数据库 helper，就是通过这种方式提供给沙箱的，而不是直接给沙箱开放无限制的宿主访问能力。
