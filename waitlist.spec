# PyInstaller build spec -- produces a standalone app that runs without
# Python or pip installed. Build natively on each target platform/arch;
# PyInstaller does not cross-compile. See BUILDING.md.
import sys

block_cipher = None

datas = [
    ('waitlist/templates', 'waitlist/templates'),
    ('waitlist/static', 'waitlist/static'),
    ('fast-audio-303101-6ae072de98ed.json', '.'),
]

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ImpactChildcareWaitlist',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep a window open so staff can see the app is running
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # native arch of the machine running the build
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='ImpactChildcareWaitlist',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='ImpactChildcareWaitlist.app',
        icon=None,
        bundle_identifier='ca.impactchildcare.waitlist',
        info_plist={
            'NSHighResolutionCapable': 'True',
            'CFBundleShortVersionString': '1.0.0',
        },
    )
