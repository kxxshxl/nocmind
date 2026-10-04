import logging
import os
import secrets
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


_INSECURE_JWT_SECRETS = {"", "change-me", "change-me-in-production", "secret", "changeme"}


def _jwt_secret() -> str:
    value = os.getenv("JWT_SECRET", "")
    if value not in _INSECURE_JWT_SECRETS and len(value) >= 32:
        return value
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters when APP_ENV=production")
    # Development: use an ephemeral random secret instead of a guessable default.
    # Tokens are invalidated on restart; set JWT_SECRET to keep sessions across restarts.
    logging.getLogger("nocmind.config").warning("JWT_SECRET missing or weak; using an ephemeral random secret")
    return secrets.token_urlsafe(48)


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "NOCMind")
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./nocmind.db")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # LLM_PROVIDER: demo | ollama | any OpenAI-compatible name (openrouter, openai, azure, ...).
    # OpenAI-compatible providers use LLM_BASE_URL (the full /chat/completions URL), LLM_API_KEY, LLM_MODEL.
    # Ollama uses OLLAMA_BASE_URL / OLLAMA_MODEL through its native /api/chat endpoint.
    llm_provider: str = os.getenv("LLM_PROVIDER", "demo")
    llm_fallback_provider: str = os.getenv("LLM_FALLBACK_PROVIDER", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4.1-mini")
    llm_api_style: str = os.getenv("LLM_API_STYLE", "openai")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
    # After a 429/5xx/timeout a provider is skipped for this many seconds instead of being retried.
    llm_cooldown_seconds: float = float(os.getenv("LLM_COOLDOWN_SECONDS", "60"))
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
    ollama_num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
    ollama_think: bool = _bool("OLLAMA_THINK", False)

    # Evaluation judge. Defaults to the primary provider; "none" disables the model judge.
    eval_judge_provider: str = os.getenv("EVAL_JUDGE_PROVIDER", "") or os.getenv("LLM_PROVIDER", "demo")

    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "sentence_transformer")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "384"))
    reranker_model: str = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    top_k: int = int(os.getenv("TOP_K", "12"))
    rerank_k: int = int(os.getenv("RERANK_K", "5"))

    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "10"))
    max_tool_rounds: int = int(os.getenv("MAX_TOOL_ROUNDS", "5"))
    max_tool_result_chars: int = int(os.getenv("MAX_TOOL_RESULT_CHARS", "4000"))
    dry_run: bool = _bool("DRY_RUN", True)
    cors_origin: str = os.getenv("CORS_ORIGIN", "http://localhost:5173")

    jwt_secret: str = field(default_factory=_jwt_secret)
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
    demo_username: str = os.getenv("DEMO_USERNAME", "admin")
    demo_password: str = os.getenv("DEMO_PASSWORD", "nocmind-admin")
    # Optional extra demo accounts for role separation; created only when a password is set.
    demo_operator_password: str = os.getenv("DEMO_OPERATOR_PASSWORD", "")
    demo_viewer_password: str = os.getenv("DEMO_VIEWER_PASSWORD", "")
    login_max_failures: int = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
    login_lockout_seconds: int = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "300"))

    cache_enabled: bool = _bool("CACHE_ENABLED", True)
    tracing_enabled: bool = _bool("TRACING_ENABLED", True)


settings = Settings()
