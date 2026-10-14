# src/config.py
"""Configuration loader for the WhatsApp bot.

Loads settings from ``config.yaml`` located at the project root and exposes
them as a dictionary ``CONFIG`` as well as convenient module‑level constants.
Some keys can be overridden by environment variables (useful in Docker/VM):

- ``WHATSAPP_DRY_RUN``  -> ``mode_dry_run`` (default ``true``)
- ``WHATSAPP_HEADLESS`` -> ``headless`` (default ``false``)
- ``WHATSAPP_CONTATOS_PERMITIDOS`` -> ``contatos_permitidos`` (JSON list; empty = nobody)
- ``WHATSAPP_PERFIL_DIR`` -> ``perfil_navegador`` (persistent Chromium profile)
- ``WHATSAPP_REGISTRO_ENTREGAS`` -> ``registro_entregas`` (delivery journal, SQLite)
- ``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_DB`` -> ``redis.*``
"""
import json
import os
from pathlib import Path
from typing import List
import yaml

from app.contrato import TIMEOUT_JOB_S as _TIMEOUT_JOB_PADRAO

# Resolve the path to ``config.yaml`` relative to this file's parent directory
CONECTOR_DIR = Path(__file__).parents[1]
CONFIG_PATH = CONECTOR_DIR / "config.yaml"

if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")

with CONFIG_PATH.open("r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f) or {}


def _env_bool(nome: str, padrao: bool) -> bool:
    valor = os.getenv(nome)
    if valor is None or valor.strip() == "":
        return padrao
    return valor.strip().lower() in ("1", "true", "yes", "sim", "on")


def carregar_contatos(valor_env, valor_config) -> List[str]:
    """Validate the allowlist: a list of exact, non-empty chat names.

    ``valor_env`` (JSON text) wins over ``valor_config`` (YAML list).
    An empty list is valid and means *no contact is served* (fail-closed).
    """
    if valor_env is not None and valor_env.strip() != "":
        try:
            valor = json.loads(valor_env)
        except json.JSONDecodeError as erro:
            raise ValueError("WHATSAPP_CONTATOS_PERMITIDOS deve ser uma lista JSON de nomes exatos") from erro
    else:
        valor = valor_config if valor_config is not None else []
    if not isinstance(valor, list) or not all(isinstance(item, str) and item.strip() for item in valor):
        raise ValueError("contatos_permitidos deve ser uma lista de nomes exatos (texto não vazio)")
    return list(dict.fromkeys(item.strip() for item in valor))


def _caminho(valor: str) -> Path:
    caminho = Path(valor).expanduser()
    return caminho if caminho.is_absolute() else CONECTOR_DIR / caminho


# Dry-run is ON unless explicitly disabled (config.yaml or WHATSAPP_DRY_RUN=false).
CONFIG["mode_dry_run"] = _env_bool("WHATSAPP_DRY_RUN", bool(CONFIG.get("mode_dry_run", True)))
CONFIG["headless"] = _env_bool("WHATSAPP_HEADLESS", bool(CONFIG.get("headless", False)))
CONFIG["contatos_permitidos"] = carregar_contatos(
    os.getenv("WHATSAPP_CONTATOS_PERMITIDOS"), CONFIG.get("contatos_permitidos")
)
CONFIG.setdefault("limite_resposta_chars", 12000)
CONFIG.setdefault(
    "mensagem_midia",
    "No momento analiso apenas mensagens de texto. Envie a informação por escrito ou o link da notícia.",
)
CONFIG.setdefault("intervalo_scanner", 1.0)
CONFIG.setdefault("limite_contexto", 15)
CONFIG.setdefault("ignorar_grupos", True)
CONFIG.setdefault("timeout_job_s", _TIMEOUT_JOB_PADRAO)
CONFIG.setdefault(
    "mensagem_indisponivel",
    "No momento não consegui analisar sua mensagem. Tente novamente em alguns minutos.",
)

# Convenience constants used throughout the codebase
DEBUG_MODE = CONFIG.get("debug_mode", True)
DEBUG_LOG_PATH = CONFIG.get("debug_log_path", "debug.log")
HEADLESS = CONFIG["headless"]
TIMEOUT_JOB_S = float(CONFIG["timeout_job_s"])
MENSAGEM_INDISPONIVEL = CONFIG["mensagem_indisponivel"]
MENSAGEM_MIDIA = CONFIG["mensagem_midia"]
CONTATOS_PERMITIDOS = CONFIG["contatos_permitidos"]
LIMITE_RESPOSTA_CHARS = int(CONFIG["limite_resposta_chars"])
PERFIL_DIR = _caminho(os.getenv("WHATSAPP_PERFIL_DIR") or CONFIG.get("perfil_navegador", "whatsapp_profile"))
REGISTRO_ENTREGAS_PATH = _caminho(
    os.getenv("WHATSAPP_REGISTRO_ENTREGAS") or CONFIG.get("registro_entregas", "data/deliveries.db")
)

# Redis configuration (used by ARQ)
REDIS_CFG = CONFIG.setdefault("redis", {})
REDIS_HOST = os.getenv("REDIS_HOST", REDIS_CFG.get("host", "127.0.0.1"))
REDIS_PORT = int(os.getenv("REDIS_PORT", REDIS_CFG.get("port", 6379)))
REDIS_DB = int(os.getenv("REDIS_DB", REDIS_CFG.get("db", 0)))


def toggle_dry_run() -> bool:
    """Toggle dry‑run mode at runtime and return the new state."""
    CONFIG["mode_dry_run"] = not CONFIG["mode_dry_run"]
    return CONFIG["mode_dry_run"]
