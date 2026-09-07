import logging
from pathlib import Path

import pytest

from htx_mcp.logging_config import (
    DEFAULT_LOG_MAX_BYTES,
    PersistentRotatingFileHandler,
    configured_log_directory,
    configured_log_max_bytes,
)


def test_log_configuration_uses_portable_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("HTX_LOG_MAX_BYTES", raising=False)
    assert configured_log_directory(str(tmp_path / "logs")) == tmp_path / "logs"
    assert configured_log_max_bytes() == DEFAULT_LOG_MAX_BYTES
    assert configured_log_max_bytes("0") == 0
    with pytest.raises(ValueError, match="non-negative integer"):
        configured_log_max_bytes("-1")


def test_persistent_rotation_keeps_uncompressed_history(tmp_path):
    path = tmp_path / "htx-mcp.log"
    handler = PersistentRotatingFileHandler(path, max_bytes=120, delay=False)
    handler.setFormatter(logging.Formatter("%(message)s"))
    test_logger = logging.getLogger("htx_mcp.rotation_test")
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.INFO)

    try:
        for index in range(20):
            test_logger.info("event-%02d-%s", index, "x" * 30)
    finally:
        handler.close()
        test_logger.handlers.clear()

    log_files = list(tmp_path.glob("htx-mcp*.log"))
    assert path in log_files
    assert len(log_files) > 2
    assert not list(tmp_path.glob("*.gz"))
    combined = "".join(file.read_text(encoding="utf-8") for file in log_files)
    for index in range(20):
        assert f"event-{index:02d}-" in combined


def test_logrotate_template_keeps_uncompressed_history():
    template = (Path(__file__).parents[1] / "scripts" / "htx-mcp.logrotate").read_text(
        encoding="utf-8"
    )

    assert "copytruncate" in template
    assert "nocompress" in template
    assert "rotate -1" in template
