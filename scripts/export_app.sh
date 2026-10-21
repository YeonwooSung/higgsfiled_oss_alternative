#!/usr/bin/env bash
# Export one app plus the shared code it needs into a standalone folder (for a separate submission).
# Usage: scripts/export_app.sh <product-lock|camera-sketch|pocket-vfx> /path/to/out
set -euo pipefail
APP="${1:?app slug, e.g. product-lock}"
OUT="${2:?output directory}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -d "$ROOT/apps/$APP" ] || { echo "unknown app: $APP" >&2; exit 1; }
mkdir -p "$OUT"
cd "$ROOT"
# The shared spec plus this app's own spec (docs/NN_<APP with underscores>_SPEC.md).
SPEC="$(ls docs | grep -i "^[0-9][0-9]_$(echo "${APP//-/_}" | tr a-z A-Z)_SPEC\.md$" || true)"
git ls-files -z -- \
  "apps/$APP" packages nodes/open_creative services infra scripts \
  "presets/$APP" "workflows/$APP" "fixtures/$APP" "models/$APP.manifest.json" \
  pyproject.toml uv.lock package.json pnpm-lock.yaml pnpm-workspace.yaml \
  LICENSE README.md .env.example .gitignore docs/00_SHARED_IMPLEMENTATION_SPEC.md ${SPEC:+"docs/$SPEC"} \
  | xargs -0 -I{} rsync -R "{}" "$OUT/"
echo "exported $APP to $OUT"
