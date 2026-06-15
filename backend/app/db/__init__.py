"""Neon PostgreSQL integration for the multi-user SaaS layer.

This package is fully additive. Paper mode never imports it, so a missing
DATABASE_URL only affects the multi-user features (auth, per-user vault, runs).
"""
