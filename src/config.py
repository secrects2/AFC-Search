from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any


@dataclass
class AppConfig:
    match_threshold: int = 85
    price_tolerance: float = 0
    request_delay_seconds: float = 3
    request_timeout_seconds: float = 20
    request_retries: int = 2
    max_results_per_product: int = 10
    enable_ocr: bool = True
    enable_screenshot: bool = True
    enable_image_match: bool = True
    image_match_threshold: int = 88
    headless: bool = True
    platforms: list[str] = field(
        default_factory=lambda: ["shopee", "momo", "yahoo", "pchome", "ruten"]
    )
    notification: dict[str, Any] = field(default_factory=dict)
    serpapi_api_key: str = ""
    brave_api_key: str = ""
    search_cache_hours: int = 24


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _simple_yaml_load(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith("  ") and current_list_key is not None:
            stripped = raw_line.strip()
            if stripped.startswith("- "):
                data[current_list_key].append(_parse_scalar(stripped[2:]))
            continue

        current_list_key = None
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "":
            data[key] = []
            current_list_key = key
        else:
            data[key] = _parse_scalar(value)

    return data


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text) or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return _simple_yaml_load(text)


def load_env_file(env_path: Path) -> None:
    """Load key=value pairs from a .env file into os.environ."""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_config(path: Path) -> AppConfig:
    config = AppConfig()

    # Load .env from project root (sibling of config.yaml)
    load_env_file(path.parent / ".env")

    if path.exists():
        loaded = _load_yaml(path)
        for field_name in AppConfig.__dataclass_fields__:
            if field_name in loaded:
                setattr(config, field_name, loaded[field_name])

    # Environment variables override config file for sensitive keys
    env_serpapi = os.environ.get("SERPAPI_API_KEY", "").strip()
    env_brave = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if env_serpapi and env_serpapi != "YOUR_SERPAPI_KEY_HERE":
        config.serpapi_api_key = env_serpapi
    if env_brave and env_brave != "YOUR_BRAVE_KEY_HERE":
        config.brave_api_key = env_brave

    return config
