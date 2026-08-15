#!/usr/bin/env bash
# Run the offline suite on a bare Python 3.10 + latest deps.
#
# WHY THIS EXISTS
# ---------------
# The project targets 3.11+ and pins its dependencies in uv.lock. This script
# deliberately does neither: it installs current releases against the oldest
# interpreter the code can still parse. That is not a substitute for
# `uv run pytest` — it is a second, differently-configured environment, which
# is the only reason it is worth running at all.
#
# WHAT A GREEN RUN HERE PROVES
#   - the suite does not depend on a pinned dependency version
#   - no test relies on 3.11+ runtime behaviour
#   - imports resolve without the lockfile
#
# WHAT IT DOES NOT PROVE
#   - nothing about the live evals: those need real GCP credentials and are
#     skipped here, so a green run says nothing about retrieval or crisis
#     accuracy
#   - nothing about deployment, Cloud Run, or Postgres
#
# The first thing it caught: test_ranking asserted `"do not" in prompt` while
# the prompt read "Do\n   NOT reorder". A substring split across a line break.
# The assertion was testing the paragraph wrapping, not the instruction.
set -euo pipefail

VENV="${SANDBOX_VENV:-/tmp/vitalvenv}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -x "$VENV/bin/python" ]; then
  echo "creating $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q \
    pytest pydantic pydantic-settings \
    langgraph langchain-core langchain-google-vertexai \
    fastapi "httpx[socks]" sse-starlette python-multipart \
    cryptography pyjwt respx
fi

# A separate config: the project's pyproject requires 3.11+, which pytest
# refuses to run under. Only the test paths are shared.
cat > "$VENV/pytest.ini" <<INI
[pytest]
rootdir = $HERE
testpaths = tests
INI

cd "$HERE"
PYTHONPATH=src:tests exec "$VENV/bin/python" -m pytest \
  -p no:cacheprovider -q --no-header -c "$VENV/pytest.ini" "$@"
