"""Least-cost energy planning core: schemas, ingestion, tariffs, finance, optimisation,
degradation, independent validation and reporting.

Everything physical is MW / MWh, everything monetary is INR, and every interval is
0.25 h. Conversions happen at the boundaries and nowhere else.
"""
__version__ = "0.1.0"
MODEL_VERSION = "lcet-model-2026.09.1"
