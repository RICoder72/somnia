"""Centralised configuration for the Quies daemon.

Resolution order for every parameter:
    1. Environment variable  (QUIES_ prefix, underscore-separated)
    2. config.yaml value     (nested YAML key)
    3. Hardcoded default     (defined once, here)

Usage:
    from settings import cfg

    cfg("scheduler.global_cooldown_minutes")   # -> 240
    cfg("budget.max_cost_per_day")             # -> 2.00
    cfg("api.model")                           # -> "claude-sonnet-4-20250514"

Env var mapping:
    QUIES_SCHEDULER__GLOBAL_COOLDOWN_MINUTES=120
    QUIES_BUDGET__MAX_COST_PER_DAY=3.00
    QUIES_API__MODEL=claude-sonnet-4-20250514

    Section and key are separated by double-underscore.
    Single underscores within a key name are preserved.
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("quies.settings")

APP_DIR = Path(os.environ.get("SOMNIA_APP_DIR", "/app"))
DATA_DIR = Path(os.environ.get("SOMNIA_DATA_DIR", "/data/somnia"))
SOLO_WORK_DIR = Path(os.environ.get("QUIES_SOLO_WORK_DIR", str(DATA_DIR / "solo-work")))
CONFIG_PATH = APP_DIR / "daemon" / "config.yaml"

# ── Defaults ────────────────────────────────────────────────────────────────
# Single source of truth for every tunable parameter.
# config.yaml and env vars override these; nothing else should hardcode them.

DEFAULTS = {
    "api": {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 8192,
        "credentials_ref": "op://Key Vault/Somnia Harvester API Key/password",
        "oauth_credentials_ref": "op://Key Vault/Claude Code OAuth/credential",
        "max_tokens_per_mode": {
            "process": 16000,
            "ruminate": 8000,
            "solo_work": 16000,
            "archaeologize": 4000,
        },
    },
    "scheduler": {
        "enabled": True,
        "check_interval_minutes": 15,
        "global_cooldown_minutes": 240,
        "rumination_cooldown_minutes": 360,
        "solo_work_cooldown_minutes": 360,
        "archaeology_cooldown_minutes": 720,
        "solo_work_max_duration_minutes": 20,
        "min_nodes_for_rumination": 5,
        "min_inbox_items": 1,
        "cycle_max_tokens": 50000,
        "cycle_max_seconds": 1200,
        "sltm_opportunistic_probability": 0.30,
        "sltm_opportunistic_limit": 8,
    },
    "budget": {
        "max_cost_per_day": 2.00,
        "max_cost_dream": 0.30,
        "max_cost_rumination": 0.30,
        "max_cost_solo_work": 1.00,
        "max_cost_archaeology": 0.30,
    },
    "graph": {
        "subgraph_depth": 3,
        "max_context_nodes": 50,
    },
    "decay": {
        "passive_cooldown_per_cycle": 0.0005,
        "sltm_threshold": 0.05,
        "pinned_floor": 0.5,
        "prune_threshold": 0.1,
        "stable_reinforcement_count": 5,
        "reinforcement_floor": 0.20,
        "dream_edge_warmth": 0.03,
        "connectivity_decay_reduction": True,
        "foundational_floor": 0.35,
        "connectivity_floor_per_edge": 0.01,
        "connectivity_floor_max": 0.30,
        "connectivity_tiers": {5: 0.75, 10: 0.50, 20: 0.25},
        "type_profiles": {
            "personality":       {"rate_multiplier": 0.05, "floor": 0.40},
            "archetype":         {"rate_multiplier": 0.10, "floor": 0.35},
            "concept":           {"rate_multiplier": 0.30, "floor": 0.20},
            "principle":         {"rate_multiplier": 0.30, "floor": 0.20},
            "insight":           {"rate_multiplier": 0.60, "floor": 0.10},
            "fact":              {"rate_multiplier": 1.00, "floor": 0.05},
            "procedure":         {"rate_multiplier": 1.00, "floor": 0.05},
            "memory":            {"rate_multiplier": 1.00, "floor": 0.00},
            "event":             {"rate_multiplier": 1.00, "floor": 0.00},
            "wondering-thread":  {"rate_multiplier": 0.70, "floor": 0.05},
        },
    },
    "edges": {
        "decay_window_days": 90,
        "decay_factor": 0.95,
        "prune_weight_threshold": 0.10,
        "archive_after_flags": 3,
    },
    "logging": {
        "level": "INFO",
        "dream_traces": True,
    },
}


# ── YAML loading ────────────────────────────────────────────────────────────

_yaml_config: dict = {}


def _load_yaml() -> dict:
    """Load config.yaml once. Returns empty dict if missing."""
    if not CONFIG_PATH.exists():
        logger.warning(f"Config not found at {CONFIG_PATH}, using defaults + env")
        return {}
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}

    # ── Legacy migration ────────────────────────────────────────────────
    # Move consolidation.min_inbox_items → scheduler.min_inbox_items
    if "consolidation" in data:
        consol = data["consolidation"]
        sched = data.setdefault("scheduler", {})
        if "min_inbox_items" in consol and "min_inbox_items" not in sched:
            sched["min_inbox_items"] = consol["min_inbox_items"]
        # Don't propagate consolidation.cooldown_minutes — it's dead
        del data["consolidation"]

    return data


def _init():
    global _yaml_config
    _yaml_config = _load_yaml()


# ── Env var resolution ──────────────────────────────────────────────────────

def _env_key(dotpath: str) -> str:
    """Convert 'scheduler.global_cooldown_minutes' → 'QUIES_SCHEDULER__GLOBAL_COOLDOWN_MINUTES'."""
    parts = dotpath.split(".", 1)
    if len(parts) == 2:
        return f"QUIES_{parts[0].upper()}__{parts[1].upper()}"
    return f"QUIES_{parts[0].upper()}"


def _coerce(value: str, target_type: type) -> Any:
    """Coerce an env var string to match the default's type."""
    if target_type is bool:
        return value.lower() in ("true", "1", "yes")
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)
    return value


# ── Public API ──────────────────────────────────────────────────────────────

def cfg(dotpath: str) -> Any:
    """Resolve a config value: env → yaml → default.

    Args:
        dotpath: Dot-separated path, e.g. "scheduler.global_cooldown_minutes"

    Returns:
        The resolved value.

    Raises:
        KeyError: If the dotpath doesn't exist in DEFAULTS (programming error).
    """
    # Split into section.key
    parts = dotpath.split(".", 1)
    if len(parts) != 2:
        raise KeyError(f"cfg() requires section.key format, got: {dotpath!r}")
    section, key = parts

    # Get default (must exist)
    if section not in DEFAULTS or key not in DEFAULTS[section]:
        raise KeyError(f"No default defined for {dotpath!r}")
    default = DEFAULTS[section][key]

    # 1. Check env var
    env_name = _env_key(dotpath)
    env_val = os.environ.get(env_name)
    if env_val is not None:
        try:
            return _coerce(env_val, type(default))
        except (ValueError, TypeError):
            logger.warning(f"Bad env value {env_name}={env_val!r}, using yaml/default")

    # 2. Check yaml
    yaml_val = _yaml_config.get(section, {}).get(key)
    if yaml_val is not None:
        return yaml_val

    # 3. Default
    return default


def cfg_section(section: str) -> dict:
    """Return an entire section merged: defaults ← yaml ← env.

    Useful for complex nested values (decay.type_profiles, api.max_tokens_per_mode)
    where individual env overrides don't make sense.
    """
    if section not in DEFAULTS:
        raise KeyError(f"No defaults for section {section!r}")

    result = dict(DEFAULTS[section])

    # Layer yaml on top
    yaml_section = _yaml_config.get(section, {})
    for k, v in yaml_section.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = {**result[k], **v}  # shallow merge for nested dicts
        else:
            result[k] = v

    # Layer env overrides for simple keys
    for k, default_val in DEFAULTS[section].items():
        if isinstance(default_val, (str, int, float, bool)):
            env_name = _env_key(f"{section}.{k}")
            env_val = os.environ.get(env_name)
            if env_val is not None:
                try:
                    result[k] = _coerce(env_val, type(default_val))
                except (ValueError, TypeError):
                    pass

    return result


def raw_yaml() -> dict:
    """Access the raw parsed yaml for edge cases that need full structure.

    Prefer cfg() or cfg_section() where possible.
    """
    return _yaml_config


# ── Initialise on import ────────────────────────────────────────────────────
_init()
