"""Detector packages. Importing this module registers all 30 patterns."""

from . import classic, smc, harmonic  # noqa: F401

__all__ = ["classic", "smc", "harmonic"]
