#!/bin/sh
set -e

cd pprof-5.17.0
# 包首次发布，npm stage publish 要求包已存在（返回 404），先直接 publish 创建包
npm publish --tag latest --access public
