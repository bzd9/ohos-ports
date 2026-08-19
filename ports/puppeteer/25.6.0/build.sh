#!/bin/sh
set -e

# Puppeteer HarmonyOS source-based build
# Clones puppeteer monorepo, applies OHOS patches, builds, publishes as @ohos-ports/puppeteer
#
# Patches:
#   0001-ohos-platform-detection.patch  — detectPlatform: openharmony → skip download
#   0002-ohos-launch-intercept.patch    — BrowserLauncher: launch() → launchViaHdc()
#   0003-ohos-coord-rounding.patch      — ElementHandle: boundingBox/clickablePoint Math.round()
#   0004-update-package-json.patch      — rename to @ohos-ports/puppeteer
#
# HdcLauncher.ts is added as a new source file (not a patch, too large for patch format).

VERSION=25.6.0
PKG=puppeteer

# 1. Download puppeteer monorepo source
echo "[build] downloading puppeteer v${VERSION} source..."
curl -fsSL "https://github.com/puppeteer/puppeteer/archive/refs/tags/puppeteer-v${VERSION}.tar.gz" -o puppeteer.tar.gz
tar -zxf puppeteer.tar.gz
rm puppeteer.tar.gz
mv "puppeteer-puppeteer-v${VERSION}" "${PKG}-${VERSION}"

cd "${PKG}-${VERSION}"

# 2. Apply patches
echo "[build] applying patches..."
patch -p1 < ../patchs/0001-ohos-platform-detection.patch
patch -p1 < ../patchs/0002-ohos-launch-intercept.patch
patch -p1 < ../patchs/0003-ohos-coord-rounding.patch
patch -p1 < ../patchs/0004-update-package-json.patch

# 3. Add HdcLauncher.ts (new source file, too large for patch format)
echo "[build] adding HdcLauncher.ts..."
cp ../files/HdcLauncher.ts packages/puppeteer-core/src/node/HdcLauncher.ts

# 4. Install dependencies
# --ignore-scripts: skip puppeteer's postinstall (downloads Chrome binary, fails on openharmony)
echo "[build] installing dependencies..."
npm install --ignore-scripts

# 5. Build
# puppeteer uses Hereby (task runner) with esbuild
echo "[build] building..."
export PATH="$(pwd)/node_modules/.bin:$PATH"
npx hereby build

# 6. Verify build output
echo "[build] verifying..."
test -f packages/puppeteer/lib/esm/puppeteer/puppeteer.js
test -f packages/puppeteer-core/lib/esm/puppeteer/node/BrowserLauncher.js
test -f packages/puppeteer-core/lib/esm/puppeteer/node/HdcLauncher.js

# Verify patches applied
NAME=$(node -e "console.log(require('./packages/puppeteer/package.json').name)")
[ "$NAME" = "@ohos-ports/puppeteer" ] || { echo "package name mismatch: $NAME" >&2; exit 1; }

grep -q "openharmony" packages/puppeteer-core/lib/esm/puppeteer/node/BrowserLauncher.js
grep -q "launchViaHdc" packages/puppeteer-core/lib/esm/puppeteer/node/BrowserLauncher.js
grep -q "Math.round" packages/puppeteer-core/lib/esm/puppeteer/api/ElementHandle.js

# 7. Smoke test: check that the built module can be imported
node -e "
const puppeteer = await import('./packages/puppeteer/lib/esm/puppeteer/puppeteer.js');
if (typeof puppeteer.default?.launch !== 'function') {
  throw new Error('launch is not a function');
}
console.log('import OK, launch type:', typeof puppeteer.default.launch);
"

echo "[build] done. Output in packages/puppeteer/"
