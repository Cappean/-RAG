"""MinerU v4 单文件 API 适配：上传 PDF，等待解析，将原始产物保存到本地。"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import shutil
import stat
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import error, request
from urllib.parse import quote, urlsplit
from uuid import uuid4

from dotenv import load_dotenv

from app.core.loggingUtils import get_logger

load_dotenv()

logger = get_logger(__name__)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "data" / "mineru"
_UNSAFE_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class MinerUError(RuntimeError):
    """适合由业务层捕获的解析错误；不包含 token、签名地址或 HTTP 响应正文。"""


@dataclass(frozen=True)
class MinerUResult:
    """本地解析结果；content_list_v2 允许缺失，由切分模块判断是否能继续。"""

    batch_id: str
    output_dir: Path
    markdown_path: Path | None
    content_list_v2_path: Path | None


class MinerUClient:
    """同步工具类；异步 Worker 中使用 asyncio.to_thread(client.parse, pdf_path)。"""

    def __init__(
        self,
        token: str | None = None,
        *,
        output_dir: str | Path | None = None,
        poll_interval: float = 5,
        poll_timeout: float = 1800,
        request_timeout: float = 120,
    ) -> None:
        self._token = (token or os.getenv("MINERU_API_KEY", "")).strip()
        if not self._token:
            raise ValueError("请配置 MINERU_API_KEY 或传入 token")
        for name, value in (
            ("poll_interval", poll_interval),
            ("poll_timeout", poll_timeout),
            ("request_timeout", request_timeout),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须为有限正数")
        self.output_dir = Path(output_dir or os.getenv("MINERU_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.request_timeout = request_timeout
        self.transfer_timeout = max(request_timeout, 600)

    def parse(
        self, pdf_path: str | Path, *, model: str = "vlm", language: str = "ch", ocr: bool = False
    ) -> MinerUResult:
        """解析单份 PDF；每次使用独立目录，完整成功后才发布结果，不覆盖历史产物。"""
        pdf = Path(pdf_path).resolve()
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
            raise ValueError("输入必须为存在的 PDF 文件")
        if not 0 < pdf.stat().st_size <= 200 * 1024 * 1024:
            raise ValueError("PDF 文件大小必须大于 0 且不超过 200MB")
        if model not in {"vlm", "pipeline"}:
            raise ValueError("model 必须为 vlm 或 pipeline")

        run_id = uuid4().hex
        output_name = self._make_output_name(pdf.stem)
        logger.info(
            "开始 MinerU 解析 run_id=%s output_name=%s bytes=%s model=%s",
            run_id,
            output_name,
            pdf.stat().st_size,
            model,
        )
        logger.debug("申请单文件上传地址 run_id=%s language=%s ocr=%s", run_id, language, ocr)
        try:
            data = self._api(
                "/file-urls/batch",
                {
                    "model_version": model,
                    "language": language,
                    "enable_formula": True,
                    "enable_table": True,
                    "files": [{"name": pdf.name, "is_ocr": ocr}],
                },
            )
            batch_id = data.get("batch_id")
            urls = data.get("file_urls")
            if not isinstance(batch_id, str) or not batch_id or not isinstance(urls, list):
                raise MinerUError("申请上传地址响应缺少 batch_id 或 file_urls")
            if len(urls) != 1 or not isinstance(urls[0], str):
                raise MinerUError("单文件上传地址数量或格式不正确")
            logger.info("MinerU 已创建批次 run_id=%s batch_id=%s", run_id, batch_id)
            self._upload(urls[0], pdf)
            logger.debug("PDF 上传完成 batch_id=%s", batch_id)
            zip_url = self._wait(batch_id)

            self.output_dir.mkdir(parents=True, exist_ok=True)
            # 下载和解压失败只清理本次临时目录，之前成功的结果不会被修改。
            with TemporaryDirectory(prefix=".pending-", dir=self.output_dir) as temporary:
                staging = Path(temporary)
                archive = staging / "result.zip"
                self._download(zip_url, archive)
                extracted = staging / "extracted"
                self._extract(archive, extracted)
                target = self._publish_result(extracted, output_name)
            result = MinerUResult(
                batch_id=batch_id,
                output_dir=target,
                markdown_path=self._find(target, "*.md"),
                content_list_v2_path=self._find(target, "*content_list_v2.json"),
            )
            logger.info("MinerU 解析完成 batch_id=%s output_dir=%s", batch_id, target)
            logger.debug("结构化输入可用=%s", result.content_list_v2_path is not None)
            return result
        except (MinerUError, OSError, zipfile.BadZipFile) as exc:
            # 不打印底层网络异常原文，其中可能带有签名 URL 或远端返回的敏感数据。
            logger.error("MinerU 解析失败 run_id=%s error_type=%s", run_id, type(exc).__name__)
            if isinstance(exc, MinerUError):
                raise
            raise MinerUError("本地文件或结果 ZIP 处理失败") from None

    @staticmethod
    def _make_output_name(file_stem: str) -> str:
        """Make a readable, Windows-safe result directory name with microsecond precision."""
        safe_stem = _UNSAFE_PATH_CHARS.sub("_", file_stem).strip(" .")
        safe_stem = safe_stem[:80].rstrip(" .") or "document"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return f"{safe_stem}_{timestamp}"

    def _publish_result(self, extracted: Path, output_name: str) -> Path:
        """Publish completed output without overwriting an existing parse of the same PDF."""
        for suffix in range(1000):
            candidate_name = output_name if suffix == 0 else f"{output_name}_{suffix:03d}"
            target = self.output_dir / candidate_name
            if target.exists():
                continue
            try:
                # Publish from a sibling temporary directory to keep the rename on-volume.
                extracted.rename(target)
                return target
            except FileExistsError:
                # Another parse may have published the same name between exists() and rename().
                continue
        raise MinerUError("无法分配唯一的 MinerU 结果目录")

    def _api(self, path: str, payload: dict | None = None, *, timeout: float | None = None) -> dict:
        """仅 MinerU API 请求携带凭据，上传和下载地址不附加 Bearer token。"""
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request("https://mineru.net/api/v4" + path, data=body, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout or self.request_timeout) as response:
                result = json.load(response)
        except error.HTTPError as exc:
            raise MinerUError(f"MinerU API 请求失败，HTTP {exc.code}") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise MinerUError("MinerU API 网络异常或响应不是有效 JSON") from None
        if not isinstance(result, dict) or result.get("code") != 0:
            raise MinerUError("MinerU API 返回业务错误")
        if not isinstance(result.get("data"), dict):
            raise MinerUError("MinerU API 响应缺少 data 对象")
        return result["data"]

    def _upload(self, url: str, pdf: Path) -> None:
        """流式上传；签名地址禁止额外添加 Content-Type，否则可能导致 OSS 验签失败。"""
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise MinerUError("上传地址必须为有效 HTTPS URL")
        connection = http.client.HTTPSConnection(parsed.netloc, timeout=self.transfer_timeout)
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        try:
            with pdf.open("rb") as source:
                connection.request(
                    "PUT", path, body=source, headers={"Content-Length": str(pdf.stat().st_size)}
                )
                response = connection.getresponse()
                if response.status not in {200, 201, 204}:
                    raise MinerUError(f"PDF 上传失败，HTTP {response.status}")
        except (OSError, http.client.HTTPException):
            raise MinerUError("PDF 上传发生网络异常") from None
        finally:
            connection.close()

    def _wait(self, batch_id: str) -> str:
        """空结果表示服务端尚未接收文件，必须继续轮询；使用单调时钟限制等待时间。"""
        deadline = time.monotonic() + self.poll_timeout
        previous_state = None
        while (remaining := deadline - time.monotonic()) > 0:
            data = self._api(
                f"/extract-results/batch/{quote(batch_id, safe='')}",
                timeout=min(self.request_timeout, remaining),
            )
            results = data.get("extract_result") or []
            if not isinstance(results, list) or len(results) > 1:
                raise MinerUError("单文件解析结果格式不正确")
            result = results[0] if results else {}
            if not isinstance(result, dict):
                raise MinerUError("解析状态格式不正确")
            state = result.get("state", "waiting-file")
            logger.debug("轮询 MinerU batch_id=%s state=%s", batch_id, state)
            if state != previous_state:
                logger.info("MinerU 状态变化 batch_id=%s state=%s", batch_id, state)
                previous_state = state
            if state == "failed":
                raise MinerUError(f"MinerU 远端解析失败，batch_id={batch_id}")
            if state == "done":
                url = result.get("full_zip_url")
                if not isinstance(url, str) or not url:
                    raise MinerUError("MinerU 已完成，但未返回结果下载地址")
                return url
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        raise MinerUError(f"MinerU 等待解析超时，batch_id={batch_id}")

    def _download(self, url: str, destination: Path) -> None:
        """流式下载 ZIP；签名地址只用于请求，不进入日志。"""
        if urlsplit(url).scheme != "https":
            raise MinerUError("结果下载地址必须为 HTTPS URL")
        logger.debug("开始下载 MinerU 结果 ZIP")
        try:
            with request.urlopen(url, timeout=self.transfer_timeout) as response:
                with destination.open("wb") as target:
                    shutil.copyfileobj(response, target)
        except (OSError, http.client.HTTPException):
            raise MinerUError("MinerU 结果下载失败") from None
        logger.debug("结果 ZIP 下载完成 bytes=%s", destination.stat().st_size)

    @staticmethod
    def _extract(archive: Path, destination: Path) -> None:
        """检查所有条目后解压，保留目录关系，拒绝越界路径、链接和过大的解压产物。"""
        root = destination.resolve()
        with zipfile.ZipFile(archive) as zipped:
            entries = zipped.infolist()
            if sum(entry.file_size for entry in entries) > 1024**3:
                raise MinerUError("ZIP 解压产物超过 1GB 上限")
            for entry in entries:
                target = (root / entry.filename).resolve()
                if not target.is_relative_to(root) or stat.S_ISLNK(entry.external_attr >> 16):
                    raise MinerUError("ZIP 包含越界路径或符号链接")
            destination.mkdir()
            zipped.extractall(destination)
        logger.debug("MinerU ZIP 解压完成 entries=%s", len(entries))

    @staticmethod
    def _find(directory: Path, pattern: str) -> Path | None:
        """递归定位原始产物，不重命名；多候选时不猜测哪个是主输入。"""
        matches = sorted(path for path in directory.rglob(pattern) if path.is_file())
        if len(matches) != 1:
            logger.debug("产物定位 pattern=%s candidates=%s", pattern, len(matches))
            return None
        return matches[0]


if __name__ == "__main__":
    mineru = MinerUClient()
    mineru.parse(r"E:\尚硅谷\实战\胃癌101患者手册_2026v2.pdf")
