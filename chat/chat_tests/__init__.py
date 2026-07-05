"""Test package for the chatbot server.

Pytest discovers tests via `testpaths = ["chat_tests"]` in
`pyproject.toml`; this `__init__.py` exists so the `chat_tests` directory
is importable as a package (e.g. for fixtures that import from
`chat_tests.conftest`).
"""

__all__ = []
