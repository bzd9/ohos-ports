#!/bin/sh
set -e

# node-gyp (node-addon-api) build: this container's node-gyp already recognizes
# openharmony/arm64 and generates a working Makefile — no cross-compile
# target/toolchain wrapper needed, same as the other node-gyp ports.

VERSION=13.0.3
PKG=better-sqlite3

curl -fsSL "https://github.com/WiseLibs/better-sqlite3/archive/refs/tags/v${VERSION}.tar.gz" -o better-sqlite3.tar.gz
tar -zxf better-sqlite3.tar.gz
rm better-sqlite3.tar.gz
# GitHub source archives extract to <repo>-<version>/, which equals
# ${PKG}-${VERSION} here, so no rename is needed.

cd "${PKG}-${VERSION}"
patch -p1 < ../patchs/0001-update-package-json.patch
patch -p1 < ../patchs/0002-openharmony-platform-support.patch

# Generate SQLite amalgamation (deps/sqlite3/sqlite3.c, deps/defines.gypi).
# The GitHub archive does not include these — they are produced by
# deps/download.sh, which fetches SQLite source from sqlite.org and runs
# `sh configure && make sqlite3.c` to create the amalgamation with the
# correct compile-time options.
# deps/download.sh has a `#!/usr/bin/env bash` shebang, but the CI container
# only ships /bin/sh, so install bash first.
brew install -y bash
npm run download

# Install dependencies (node-addon-api) and node-gyp.
# --ignore-scripts: skip the "prepare" script (which would try to compile)
# so we can run each step ourselves and check its output.
npm install --ignore-scripts
npm install --ignore-scripts --no-save node-gyp
export PATH="$(pwd)/node_modules/.bin:$PATH"

# Compile C++ native addon from source (src/better_sqlite3.cpp + deps/sqlite3)
export CC=clang CXX=clang++
node-gyp rebuild --release --force_build=1

test -f build/Release/better_sqlite3.node

# Create the openharmony-arm64 prebuild so getPrebuildPath() in lib/binding.js
# finds it at runtime (PREBUILD_PLATFORMS now includes 'openharmony').
mkdir -p prebuilds
cp build/Release/better_sqlite3.node prebuilds/openharmony-arm64.node

llvm-strip --strip-all prebuilds/openharmony-arm64.node
binary-sign-tool sign -selfSign 1 -inFile prebuilds/openharmony-arm64.node -outFile prebuilds/openharmony-arm64.node.signed
mv prebuilds/openharmony-arm64.node.signed prebuilds/openharmony-arm64.node
chmod +x prebuilds/openharmony-arm64.node

# Remove build/ directory — not needed in the published package, the prebuild
# in prebuilds/openharmony-arm64.node is what getPrebuildPath() resolves to.
rm -rf build

# --- verify package contents ---

NAME=$(node -e "console.log(require('./package.json').name)")
[ "$NAME" = "@ohos-ports/better-sqlite3" ]

node --check lib/binding.js
grep -q "'openharmony'" lib/binding.js

readelf -h prebuilds/openharmony-arm64.node | grep -q 'AArch64'
readelf -S prebuilds/openharmony-arm64.node | grep -q '\.codesign'

# Real functional smoke test: this container IS the target platform, so
# actually load the addon and exercise core SQLite operations instead of
# only parsing the ELF header.
node -e '
  const Database = require("./");
  const db = new Database(":memory:");
  db.exec("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)");
  db.prepare("INSERT INTO test (name) VALUES (?)").run("hello");
  const row = db.prepare("SELECT * FROM test").get();
  if (!row || row.name !== "hello") {
    throw new Error("unexpected query result: " + JSON.stringify(row));
  }
  db.close();
  console.log("better-sqlite3 smoke test passed");
'
