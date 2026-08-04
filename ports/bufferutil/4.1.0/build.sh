#!/bin/sh
set -e

curl -fsSL https://github.com/websockets/bufferutil/archive/refs/tags/v4.1.0.tar.gz -o bufferutil-4.1.0.tar.gz
tar -zxf bufferutil-4.1.0.tar.gz
rm bufferutil-4.1.0.tar.gz
cd bufferutil-4.1.0
patch -p1 < ../patchs/0001-update-package-json.patch

npm install
npm run prebuild

# 把其他平台的预构建产物复制到包里面一起发布
cd ..
curl -fsSL https://registry.npmjs.org/bufferutil/-/bufferutil-4.1.0.tgz -o bufferutil-4.1.0.tgz
tar -zxf bufferutil-4.1.0.tgz
rm bufferutil-4.1.0.tgz

cp -r package/prebuilds/* bufferutil-4.1.0/prebuilds/
rm -rf package

cd bufferutil-4.1.0/prebuilds
mv darwin-arm64/bufferutil.node darwin-arm64/@ohos-ports+bufferutil.node
mv darwin-x64/bufferutil.node darwin-x64/@ohos-ports+bufferutil.node
mv linux-x64/bufferutil.node linux-x64/@ohos-ports+bufferutil.node
mv win32-ia32/bufferutil.node win32-ia32/@ohos-ports+bufferutil.node
mv win32-x64/bufferutil.node win32-x64/@ohos-ports+bufferutil.node
