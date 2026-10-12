#!/usr/bin/env bash
set -e

echo "=== 1. Ruff Lint ==="
.venv/bin/ruff check src/ tests/

echo "=== 2. Ruff Format Check ==="
.venv/bin/ruff format --check src/ tests/

echo "=== 3. Static Type Check (ty) ==="
.venv/bin/ty check src/
.venv/bin/ty check tests/

echo "=== 4. Unit Test Suite (pytest) ==="
.venv/bin/pytest tests/unit/ -v

echo "=== All Quality Gates Passed! ==="
