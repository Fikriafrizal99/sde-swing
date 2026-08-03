"""Canonical multi-source market-data layer for SDE Swing V1.7.0.

This package isolates external market-data providers behind a canonical
schema so the Final Decision Engine never learns the original source of any
record.  The flow is intentionally linear:

    Source Client -> Adapter -> Canonical Mapper -> Data Quality
        -> Conflict Resolver -> Source Router -> Feature Engine

Nothing in this package makes trading decisions.  It only produces typed,
provenance-tagged canonical records plus quality and health telemetry.
"""
