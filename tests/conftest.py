import os
import tempfile
from pathlib import Path

# The production default intentionally publishes only semantic tools. These
# tests exercise the complete compatibility surface as well.
os.environ["HTX_TOOLSETS"] = "all"
# Most compatibility tests intentionally assert legacy low-level mappings. The
# production configuration still defaults semantic swap tools to v5.
os.environ["HTX_SWAP_API_VERSION"] = "legacy"
os.environ["HTX_LOG_DIR"] = str(
    Path(tempfile.gettempdir()) / f"htxmcp-tests-{os.getpid()}"
)
