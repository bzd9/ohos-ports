#!/bin/sh
set -e

cd bufferutil-4.1.0
npm stage publish --tag latest --access public
