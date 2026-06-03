"""Data ingestion layer: news + market history for the A-share universe.

Public entry point is :func:`get_provider`, which returns an AkShare-backed
provider when available and otherwise a deterministic mock provider so the
pipeline always runs.
"""
from .provider import DataProvider, get_provider
from .warehouse import Warehouse

__all__ = ["DataProvider", "get_provider", "Warehouse"]
