---
title: "PR #67 CI failure: ROCm torch pin vs hosted runners"
author: Jared
date: 2026-10-27
branch: worktree-gpu-rerank-reverify
touches:
  - requirements.txt
---

## Decided
Pin torch as a bare version (`torch==2.13.0`) in requirements.txt, never a build-variant pin
(`torch==2.13.0+rocm7.2`) with a blanket `--extra-index-url`. PEP 440 equality ignores the local
version segment, so a bare pin is satisfied by whatever variant (ROCm locally, CPU in CI/Docker)
is already installed -- confirmed with `packaging.specifiers.SpecifierSet('==2.13.0')` containing
both `Version('2.13.0+rocm7.2')` and `Version('2.13.0+cpu')`. CI and the Dockerfile each install a
matching `torch==2.13.0` CPU build in an earlier step/layer so nothing redownloads.

## Tried and ruled out
A prior session pinned the ROCm variant directly in requirements.txt to stop a routine
`pip install -r requirements.txt` from silently downgrading the local GPU build back to CPU. That
protection is real and worth keeping, but pinning the *variant* (not just the version) broke both
CI jobs on the hosted ubuntu-latest runner: pip tried to download the 6.2GB ROCm wheel and hit the
runner's disk quota (`OSError: [Errno 122] Disk quota exceeded`). The Dockerfile's existing
"install CPU torch first in its own layer" pattern did NOT make that requirements.txt line a
no-op either, because the variant+version string genuinely differed from what was pre-installed.

## Watch out
Any future torch (or similar multi-variant package: onnxruntime, tensorflow, etc.) pin that needs
to survive both a GPU dev machine and a GPU-less CI/Docker environment should pin the bare version
only, and let each environment install its own matching variant in an earlier step. Don't assume
"install the CPU build first" alone prevents a redownload -- it only works if the *requirements.txt
pin itself* is variant-agnostic.
