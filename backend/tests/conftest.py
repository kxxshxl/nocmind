import os
import tempfile

# Isolated, offline configuration for the test suite (set before any app module is imported).
_tmp = tempfile.mkdtemp(prefix="nocmind-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/api.db"
os.environ["LLM_PROVIDER"] = "demo"
os.environ["LLM_FALLBACK_PROVIDER"] = ""
os.environ["EVAL_JUDGE_PROVIDER"] = "none"
os.environ["CACHE_ENABLED"] = "false"
os.environ["DEMO_OPERATOR_PASSWORD"] = "operator-test-pw"
os.environ["DEMO_VIEWER_PASSWORD"] = "viewer-test-pw"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
