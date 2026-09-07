import os
import tempfile
from pathlib import Path

# The production default intentionally publishes only semantic tools. These
# tests exercise the complete compatibility surface as well.
os.environ["HTX_TOOLSETS"] = "all"
os.environ["HTX_LOG_DIR"] = str(
    Path(tempfile.gettempdir()) / f"htxmcp-tests-{os.getpid()}"
)
