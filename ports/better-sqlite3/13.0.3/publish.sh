#!/bin/sh
set -e

cd better-sqlite3-13.0.3
# --ignore-scripts: build.sh 已完成全部准备工作（node-gyp 编译、签名、prebuild 生成），
# 跳过 prepublishOnly（upstream 的 prepare 脚本会尝试重新编译，无需重复）
npm publish --ignore-scripts --provenance --tag latest --access public
