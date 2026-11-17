#!/usr/bin/env bash
# Rebuilds ComfyUI-Studio/web/vendor/three/: three.js plus the add-ons the 3D viewer (web/glbview.js) uses, as one
# minified ES module, and the Draco decoder for compressed .glb files. Studio serves these itself, so the viewer works
# offline. Needs node and npm (only to run this script). Usage: bash scripts/vendor-three.sh [version]
set -euo pipefail
VERSION="${1:-0.187.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/ComfyUI-Studio/web/vendor/three"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"
npm init -y >/dev/null
npm install --silent --no-audit --no-fund "three@$VERSION" esbuild@0.27 >/dev/null
cat > entry.js <<'JS'
export * from "three";
export { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
export { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
export { OrbitControls } from "three/addons/controls/OrbitControls.js";
export { TransformControls } from "three/addons/controls/TransformControls.js";
export { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
export { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
JS
rm -rf "$OUT" && mkdir -p "$OUT/draco"
npx esbuild entry.js --bundle --format=esm --minify --legal-comments=none --log-level=warning \
  --banner:js="/* three.js r$(echo "$VERSION" | cut -d. -f2) (MIT, see LICENSE) with GLTFLoader, DRACOLoader, OrbitControls, TransformControls, RoomEnvironment, MeshoptDecoder. Built by scripts/vendor-three.sh */" \
  --outfile="$OUT/three.bundle.min.js"
cp node_modules/three/examples/jsm/libs/draco/gltf/draco_decoder.wasm node_modules/three/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js "$OUT/draco/"
cp node_modules/three/LICENSE "$OUT/LICENSE"
cp node_modules/three/examples/jsm/libs/draco/README.md "$OUT/draco/README.md" 2>/dev/null || true
echo "$VERSION" > "$OUT/VERSION"
ls -la "$OUT" "$OUT/draco"
