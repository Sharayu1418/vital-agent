#!/usr/bin/env bash
# One palette, two bundlers.
#
# Expo and Next each bundle from their own tree and this repo has no
# workspace tooling, so sky.js has to exist in both places. The web copy is
# the source of truth. tests/sky.test.mjs asserts the two files are
# byte-identical, so forgetting to run this fails the build rather than
# shipping two subtly different skies.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$here/../vital-mobile/lib"
cp "$here/app/lib/sky.js" "$here/../vital-mobile/lib/sky.js"
echo "synced sky.js -> vital-mobile/lib/sky.js"
