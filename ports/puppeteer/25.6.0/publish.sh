#!/bin/sh
set -e

cd puppeteer-25.6.0/packages/puppeteer

# Publish @ohos-ports/puppeteer
# --provenance: generate provenance signature
# --access public: scoped package default is restricted, make public
# npm stage publish: stage for 2FA review, maintainer approves on npmjs.com
npm stage publish --provenance --tag latest --access public
