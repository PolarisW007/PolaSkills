#!/usr/bin/env python3
"""Safe fixture script."""

from __future__ import annotations


def normalize(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be empty")
    return value.strip().lower()
