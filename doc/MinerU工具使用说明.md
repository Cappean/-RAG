# MinerU 工具使用说明

更新时间：2026-10-19。当前实现为单 PDF 云端解析工具，尚未接入 FastAPI、内存任务队列、切分或 MinIO。

## 1. 代码与产物位置

工具代码位于 `后端/src/app/infrastructure/mineru.py`，通过 `MinerUClient` 封装 MinerU v4 HTTP API。它负责申请上传地址、上传、轮询、下载和解压；解析结果如何切分、是否入库由知识库模块决定。

默认输出位置与当前工作目录无关，固定为后端项目下的 `data/mineru`，每次调用生成“清理后的原 PDF 文件名 + 本地时间戳”目录，时间精确到微秒：

```text
后端/data/mineru/
└── 肺癌诊疗指南_20261019_183052_123456/
    ├── auto/                    示例 ZIP 原有目录，可能不存在
    │   ├── full.md
    │   ├── …content_list_v2.json
    │   └── images/
    └── 其他 MinerU 原始产物
```

只移除了工具额外添加的 `parser-output/` 层。ZIP 如果自带 `auto/` 等目录，会完整保留；文件和图片均不重命名、不移动，避免破坏 Markdown 中的相对路径。非法文件名字符会替换为下划线，名称主体最长 80 字符。时间戳冲突时追加序号，不覆盖已有结果。下载 ZIP 使用临时目录，成功发布后只保留解压产物。

`data/` 已加入 `.gitignore`。这是 Demo 的本地结果目录，不是 MinIO 实现；后续知识库 Service 可将产物上传到 `knowledge/{documentId}/parser-output/`，并记录对应对象键。

## 2. 配置与调用

在 `后端/.env` 中配置：

```dotenv
MINERU_API_KEY=你的MinerU令牌
LOG_LEVEL=DEBUG
LOG_COLOR=auto
# 可选；不设置时使用后端/data/mineru。
MINERU_OUTPUT_DIR=
```

也可通过构造参数传入 token、output_dir。输出目录的显式相对路径基于当前工作目录；生产部署可传绝对路径。日志初始化由调用入口执行一次，工具内部只获取 logger，不修改全局日志配置。

在 `后端` 目录开发时，先将 `src` 加入 Python 导入路径：

```powershell
$env:PYTHONPATH = 'src'
```

业务代码中的最小调用示例：

```python
from app.core.loggingUtils import get_logger, setup_logging
from app.infrastructure.mineru import MinerUClient

setup_logging()  # 启动入口初始化一次；默认 DEBUG，LOG_LEVEL 可覆盖。
logger = get_logger(__name__)

client = MinerUClient()
result = client.parse(r"E:\资料\指南.pdf")
logger.info("解析完成 batch_id=%s directory=%s", result.batch_id, result.output_dir)
logger.debug("切分输入=%s", result.content_list_v2_path)
```

返回 `MinerUResult`，包含 batch_id、产物目录、Markdown 路径和 content_list_v2 路径。递归查找时只有一个匹配文件才返回路径，缺失或多个匹配均返回 `None`，调用方应校验，不静默改用另一种结构化格式。

解析参数默认 `model="vlm"`、`language="ch"`、`ocr=False`；可通过 `client.parse(path, ocr=True)` 指定 OCR。保留公式与表格解析。当前只支持单 PDF，不包含批量、断点续传和自动重试。

## 3. 异步调用与错误

工具内部使用同步标准库网络调用。FastAPI 或异步 Worker 应交给线程执行，不能直接在事件循环中调用：

```python
import asyncio

result = await asyncio.to_thread(client.parse, pdf_path)
```

取消等待线程的协程不会停止已经开始的线程或远端解析。当前轮询超时为 1800 秒，轮询间隔为 5 秒，API 单次网络超时为 120 秒，上传和下载网络超时至少为 600 秒；网络超时是 socket 等待限制，不代表整个流程的硬截止时间。构造参数可调整前三项。

无效配置与输入抛出 `ValueError`；远端错误、等待超时、下载或本地结果处理失败抛出 `MinerUError`。日志记录批次、状态变化和产物位置，详细轮询信息使用 DEBUG，关键阶段使用 INFO，不输出 token、签名 URL、响应正文或解析全文。

上传使用不含 Content-Type 的签名 PUT，避免破坏 OSS 签名。ZIP 解压拒绝越界路径和符号链接，解压后总大小上限为 1GB。PDF 本地校验扩展名及 200MB 大小限制；页数和真实格式由远端接口校验。

## 4. 验证范围

已完成隔离测试：成功流程与 ZIP 原始目录保留、可读时间戳目录和文件名清理、参数校验、非法输入、ZIP 越界拦截、网络错误脱敏、空轮询结果、远端失败和上传请求头。使用模拟网络响应与本地 ZIP；未调用真实 MinerU API、未生成真实文档解析产物，也未验证额度和远端可用性。

旧打包配置仍指向已删除的 `medical_consultation_backend` 包，整体框架与 CLI 尚待恢复；本工具在 `PYTHONPATH=src` 下可导入使用。
