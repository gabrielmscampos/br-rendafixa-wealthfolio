"""API settings, read from environment variables."""

import datetime as dt
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from br_fixed_income.sources import default_tesouro_cache


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    bonds_file: Path = Path("bonds.yaml")
    # None disables writing CSV files; quotes are still served by the API.
    output_dir: Path | None = Path("quotes")
    refresh_interval: dt.timedelta = dt.timedelta(hours=6)
    use_tesouro_api: bool = True
    # None downloads the Tesouro CSV on every refresh.
    tesouro_cache: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        defaults = cls()
        output_dir = env.get("OUTPUT_DIR")
        return cls(
            bonds_file=Path(env.get("BONDS_FILE") or defaults.bonds_file),
            output_dir=(
                defaults.output_dir
                if output_dir is None
                else (Path(output_dir) if output_dir else None)
            ),
            refresh_interval=dt.timedelta(
                minutes=_positive_int(
                    env,
                    "REFRESH_INTERVAL_MINUTES",
                    int(defaults.refresh_interval.total_seconds() // 60),
                )
            ),
            use_tesouro_api=_bool(
                env, "USE_TESOURO_API", defaults.use_tesouro_api
            ),
            tesouro_cache=default_tesouro_cache(env),
            host=env.get("HOST") or defaults.host,
            port=_positive_int(env, "PORT", defaults.port),
        )


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(
            f"{name} must be an integer (got: {raw!r})"
        ) from None
    if value <= 0:
        raise ConfigError(f"{name} must be positive (got: {raw!r})")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if not raw:
        return default
    if raw.strip().lower() in _TRUE:
        return True
    if raw.strip().lower() in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false (got: {raw!r})")
