"""Application settings loaded from environment (.env). No secrets hardcoded."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv optional at import time
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False

load_dotenv()

_BASE_DIR = Path(__file__).resolve().parents[2]


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Centralised, env-driven configuration."""

    base_dir: Path = field(default=_BASE_DIR)
    gl_path: Path = field(default_factory=lambda: Path(os.getenv("GL_PATH", "samples/gl_sample.csv")))
    lhdn_path: Path = field(default_factory=lambda: Path(os.getenv("LHDN_PATH", "samples/lhdn_sample.json")))
    output_path: Path = field(default_factory=lambda: Path(os.getenv("OUTPUT_PATH", "output/reconciliation_summary.xlsx")))
    date_tolerance_days: int = field(default_factory=lambda: _get_int("DATE_TOLERANCE_DAYS", 2))
    amount_tolerance_rm: float = field(default_factory=lambda: _get_float("AMOUNT_TOLERANCE_RM", 0.05))
    sst_tolerance_rm: float = field(default_factory=lambda: _get_float("SST_TOLERANCE_RM", 0.05))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", "gemini/gemini-3.6-flash"))
    ai_vision_model: str = field(default_factory=lambda: os.getenv("AI_VISION_MODEL", "gemini-3.8-flash"))
    ai_vision_base_url: str = field(default_factory=lambda: os.getenv(
        "AI_VISION_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"))
    embed_model: str = field(default_factory=lambda: os.getenv("AI_EMBED_MODEL", "all-MiniLM-L6-v2"))
    anomaly_threshold: float = field(default_factory=lambda: _get_float("ANOMALY_THRESHOLD", 85.0))

    def resolve(self) -> "Settings":
        """Return a copy with paths resolved relative to the project root."""
        def _resolve(p: Path) -> Path:
            return p if p.is_absolute() else (self.base_dir / p)

        return Settings(
            base_dir=self.base_dir,
            gl_path=_resolve(self.gl_path),
            lhdn_path=_resolve(self.lhdn_path),
            output_path=_resolve(self.output_path),
            date_tolerance_days=self.date_tolerance_days,
            amount_tolerance_rm=self.amount_tolerance_rm,
            sst_tolerance_rm=self.sst_tolerance_rm,
            log_level=self.log_level,
            ai_model=self.ai_model,
            ai_vision_model=self.ai_vision_model,
            ai_vision_base_url=self.ai_vision_base_url,
            embed_model=self.embed_model,
            anomaly_threshold=self.anomaly_threshold,
        )


settings = Settings().resolve()
