"""Pytest configuration for the basketball-data-emporium test suite.

We do NOT add any project-wide fixtures here — the existing tests
build their own clients with explicit dependency overrides. This
file exists so `tests/` is discoverable as a package if we ever add
shared fixtures, and to make pytest's rootdir explicit.
"""
