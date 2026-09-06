import os


# The production default intentionally publishes only semantic tools. These
# tests exercise the complete compatibility surface as well.
os.environ["HTX_TOOLSETS"] = "all"
