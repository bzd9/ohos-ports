#!/bin/sh
set -e

cd resvg-js-2.6.2
# --ignore-scripts: build.sh 已完成全部准备工作（cargo build、patch、签名），
# 跳过 prepublishOnly（napi prepublish — napi-rs 不支持 openharmony）
npm stage publish --ignore-scripts --provenance --tag latest --access public
