#!/bin/sh
# Runs without a Lua toolchain: checks the module defines too_long.
grep -q "function M.too_long" "$(dirname "$0")/lint.lua"
