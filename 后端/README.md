# 医疗智能问诊后端基础框架

> 当前状态更新（2026-10-19）：旧应用源码已删除，源码改为 `src/app` 布局。已新增日志工具与单文件 MinerU API 工具，使用方式见 [MinerU工具使用说明](../doc/MinerU工具使用说明.md)。开发时设置 `PYTHONPATH=src`。下方初始化记录中的 MongoDB 队列与独立 Worker 已被内存队列设计替代，旧 CLI 和打包入口尚未恢复，不能作为当前可运行说明。

当前阶段只搭建后端边界和异步任务基础设施，暂不联调前端，不加载模型，不访问 Milvus、MinIO、DeepSeek，也不实现鉴权。

使用 Python 3.13（`.python-version` 已固定）+ FastAPI + PyMongo Async + uv。框架说明见上级 `doc/后端项目框架设计.md`。

## 本地初始化

```powershell
uv sync --dev
Copy-Item .env.example .env
uv run pytest
uv run ruff check .
```

## 启动 API

```powershell
uv run medical-api
```

健康检查：`GET http://127.0.0.1:8000/api/health/live`。`dependenciesChecked=false` 表示当前只确认 API 进程存活。

## WSL 运行方式

目前可以在 Windows 运行 API/Worker，通过配置访问 WSL 中的 MongoDB 等设施。端口是否可达留待联调时确认。

也可以在 WSL 运行 API/Worker。Windows 初始化的 `.venv` 不能直接在 WSL 使用；进入映射目录后，为 WSL 单独建立环境，避免覆盖 Windows 环境：

```bash
export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv sync --dev
uv run medical-api
```

以下命令会访问 MongoDB，只在后续确认地址后执行。Worker 在独立终端运行：

```bash
# 每个 WSL 终端都先 export UV_PROJECT_ENVIRONMENT=.venv-wsl
uv run medical-init-db
uv run medical-worker
# 另一个终端可提交验证任务：
uv run medical-enqueue-demo
```

MongoDB 任务集合使用租约领取、心跳续租、失败重试和最大尝试次数。暂时不引入 Redis；后续若任务量或跨机器调度需要，再替换 `tasks/repository.py` 的实现，不改 Worker 业务接口。

## 目录职责

```text
src/medical_consultation_backend/
├── api/              HTTP 路由和协议边界
├── core/             配置、统一错误结构
├── infrastructure/   MongoDB 等外部设施适配
├── modules/           患者、会话、知识库、RAG 业务模块预留
└── tasks/            MongoDB 队列、Worker、任务处理器
```

开发过程记录在上级 `doc/后端项目开发日志.md`。运行时日志系统暂不设计；代码只保留极少数 Worker 故障告警，避免把医疗内容写入输出。
