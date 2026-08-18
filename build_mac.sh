#!/usr/bin/env bash
# Builds a standalone macOS app (double-click, no Python/pip needed to run).
#
# Run this natively on the target architecture -- on Apple Silicon it
# produces an arm64 build, on an Intel Mac it produces an x86_64 build.
# PyInstaller cannot cross-build between the two from one machine, so if you
# need both, run this script once on each kind of Mac.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv build_venv
source build_venv/bin/activate
pip install -r requirements.txt
pip install "pyinstaller>=6.15.0"

pyinstaller --clean --noconfirm waitlist.spec

deactivate

echo ""
echo "Done. App: dist/ImpactChildcareWaitlist.app"
echo "Architecture: $(uname -m)"
echo ""
echo "First launch on someone else's Mac: right-click the app -> Open (once),"
echo "to get past Gatekeeper's unsigned-app warning."
