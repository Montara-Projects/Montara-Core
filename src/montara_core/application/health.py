"""Minimal process health use case; no infrastructure dependencies."""


def health() -> dict[str, str]:
    return {"status": "ok"}
