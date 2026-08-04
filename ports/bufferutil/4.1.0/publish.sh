#!/bin/sh
set -e

cd bufferutil-4.1.0
# --provenance 生成来源证明
npm stage publish --provenance --tag latest --access public
