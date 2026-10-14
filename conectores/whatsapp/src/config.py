# src/config.py
"""Configuration loader for the WhatsApp bot.

Loads settings from ``config.yaml`` located at the project root and exposes
them as a dictionary ``CONFIG`` as well as convenient module‑level constants.
"""
import os
from pathlib import Path
import yaml

# Resolve the path to ``config.yaml`` relative to this file's parent directory
CONFIG_PATH = Path(__file__).parents[1] / "config.yaml"

if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")

with CONFIG_PATH.open("r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# Convenience constants used throughout the codebase
# Mutable flag for dry‑run mode (can be toggled at runtime)
DEBUG_MODE = CONFIG.get("debug_mode", True)
DEBUG_LOG_PATH = CONFIG.get("debug_log_path", "debug.log")

# Redis configuration (used by ARQ)
REDIS_CFG = CONFIG.get("redis", {})
REDIS_HOST = REDIS_CFG.get("host", "127.0.0.1")
REDIS_PORT = REDIS_CFG.get("port", 6379)
REDIS_DB = REDIS_CFG.get("db", 0)

# Groq configuration
GROQ_CFG = CONFIG.get("groq", {})
GROQ_API_KEY = os.getenv("GROQ_API_KEY", GROQ_CFG.get("api_key"))
GROQ_MODELO_PRINCIPAL = GROQ_CFG.get("modelo_principal", "qwen/qwen3.8-27b")
GROQ_MODELO_FALLBACK = GROQ_CFG.get("modelo_fallback", "openai/gpt-oss-120b")
