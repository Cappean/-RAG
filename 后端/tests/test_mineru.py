from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from app.infrastructure.mineru import MinerUClient, MinerUError


def make_client(output_dir: Path) -> MinerUClient:
    return MinerUClient(
        "test-token",
        output_dir=output_dir,
        poll_interval=0.01,
        poll_timeout=1,
        request_timeout=1,
    )


def test_client_requires_token_and_positive_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MINERU_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MINERU_API_KEY"):
        MinerUClient("", output_dir=tmp_path)
    with pytest.raises(ValueError, match="poll_interval"):
        MinerUClient("test-token", output_dir=tmp_path, poll_interval=0)


def test_parse_preserves_output_layout_in_unique_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "指南.pdf"
    pdf.write_bytes(b"pdf")
    output_dir = tmp_path / "results"
    archive = tmp_path / "mineru.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("auto/content_list_v2.json", json.dumps({"pages": []}))
        zipped.writestr("auto/full.md", "# 指南")
        zipped.writestr("auto/images/page-1.jpg", b"image")

    client = make_client(output_dir)
    monkeypatch.setattr(
        client,
        "_api",
        lambda path, payload=None, **kwargs: {
            "batch_id": "batch-1",
            "file_urls": ["https://upload.invalid/signed"],
        },
    )
    monkeypatch.setattr(client, "_upload", lambda url, source: None)
    monkeypatch.setattr(client, "_wait", lambda batch_id: "https://download.invalid/signed")

    def copy_archive(url: str, destination: Path) -> None:
        destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr(client, "_download", copy_archive)
    result = client.parse(pdf)

    assert result.batch_id == "batch-1"
    assert result.output_dir.parent == output_dir
    assert re.fullmatch(r"指南_\d{8}_\d{6}_\d{6}", result.output_dir.name)
    assert "parser-output" not in {part.name for part in result.output_dir.parents}
    assert result.markdown_path is not None
    assert result.markdown_path.read_text(encoding="utf-8") == "# 指南"
    assert result.content_list_v2_path is not None
    assert (result.output_dir / "auto" / "images" / "page-1.jpg").read_bytes() == b"image"


def test_result_name_sanitizes_filename_and_limits_length() -> None:
    result_name = MinerUClient._make_output_name("../<a very long title>" + "x" * 100)

    assert "/" not in result_name
    assert "<" not in result_name
    assert len(result_name.rsplit("_", 3)[0]) <= 80


def test_parse_rejects_non_pdf_before_network_calls(tmp_path: Path) -> None:
    text_file = tmp_path / "report.txt"
    text_file.write_text("not a PDF", encoding="utf-8")
    client = make_client(tmp_path / "results")

    with pytest.raises(ValueError, match="PDF"):
        client.parse(text_file)


def test_extract_rejects_zip_slip(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../outside.txt", "not allowed")

    with pytest.raises(MinerUError, match="越界路径"):
        MinerUClient._extract(archive, tmp_path / "output")
    assert not (tmp_path / "outside.txt").exists()


def test_api_network_errors_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure import mineru

    client = make_client(Path("unused"))
    monkeypatch.setattr(
        mineru.request, "urlopen", Mock(side_effect=URLError("private response content"))
    )

    with pytest.raises(MinerUError, match="网络异常") as error:
        client._api("/file-urls/batch", {})
    assert "private response content" not in str(error.value)
    assert "test-token" not in str(error.value)


def test_poll_waits_for_initially_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(Path("unused"))
    responses = Mock(
        side_effect=[
            {"extract_result": []},
            {"extract_result": [{"state": "running"}]},
            {"extract_result": [{"state": "done", "full_zip_url": "https://example.test/out.zip"}]},
        ]
    )
    monkeypatch.setattr(client, "_api", responses)
    monkeypatch.setattr("app.infrastructure.mineru.time.sleep", lambda seconds: None)

    assert client._wait("batch-1") == "https://example.test/out.zip"
    assert responses.call_count == 3


def test_poll_failed_task_raises_without_remote_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(Path("unused"))
    monkeypatch.setattr(
        client,
        "_api",
        Mock(
            return_value={
                "extract_result": [{"state": "failed", "err_msg": "private server message"}]
            }
        ),
    )
    with pytest.raises(MinerUError, match="远端解析失败") as error:
        client._wait("batch-1")
    assert "private server message" not in str(error.value)


def test_upload_does_not_add_content_type_or_api_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"pdf")
    connection = Mock()
    connection.getresponse.return_value.status = 200
    monkeypatch.setattr(
        "app.infrastructure.mineru.http.client.HTTPSConnection", Mock(return_value=connection)
    )
    make_client(tmp_path)._upload("https://upload.test/file?signature=secret", pdf)

    assert connection.request.call_args.kwargs["headers"] == {"Content-Length": "3"}
    connection.close.assert_called_once()
