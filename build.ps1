$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw '缺少 .venv；请先按 README 安装项目依赖。'
}
& $python -m pip install 'pyinstaller>=6.15,<7'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 安装失败' }
$upstreamRoot = & $python -c 'import pathlib, wechatauto; print(pathlib.Path(wechatauto.__file__).parent.parent)'
if ($LASTEXITCODE -ne 0) { throw '找不到微信适配库' }
& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name WxReply --icon assets\wxreply.ico `
    --paths $upstreamRoot --collect-data wechatauto --collect-data wxreply `
    --collect-binaries uiautomation `
    --hidden-import win32timezone launch_gui.py
if ($LASTEXITCODE -ne 0) { throw 'EXE 构建失败' }
Write-Host "构建完成：$PSScriptRoot\dist\WxReply.exe"
