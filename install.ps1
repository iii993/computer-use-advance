<#
.SYNOPSIS
  把 computer-use-advance 挂进 DSH profile: 自动探测本机 python 解释器与仓库路径。
.DESCRIPTION
  改写仓库根目录 cordis.patch.yml 里的 command / args, 使其指向本机的 python 与
  computer_mcp/server.py、krita_mcp/mcp_server.py, 然后打印后续步骤。
.PARAMETER Python
  指定 python 解释器绝对路径; 省略则从 PATH 探测。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Python H:\PY\python.exe
#>
param(
  [string]$Python,
  [string]$Profile = 'web'
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
if (-not $repo) { $repo = (Get-Location).Path }

if (-not $Python) {
  $c = Get-Command python -ErrorAction SilentlyContinue
  if ($c) { $Python = $c.Source }
  elseif (Test-Path 'H:\PY\python.exe') { $Python = 'H:\PY\python.exe' }
  else { throw 'PATH 里找不到 python, 请用 -Python <完整路径> 指定' }
}

$patch = Join-Path $repo 'cordis.patch.yml'
if (-not (Test-Path $patch)) { throw "找不到 $patch" }

$raw = Get-Content $patch -Raw -Encoding UTF8
$q = "'"
$raw = $raw -replace "(?m)^(\s*command:\s*)'.*?'", ('$1' + $q + $Python + $q)
$raw = $raw -replace "(?m)^(\s*-\s*)'.*computer_mcp.*?'", ('$1' + $q + $repo + '\computer_mcp\server.py' + $q)
$raw = $raw -replace "(?m)^(\s*-\s*)'.*krita_mcp.*?'", ('$1' + $q + $repo + '\krita_mcp\mcp_server.py' + $q)
Set-Content -Path $patch -Value $raw -Encoding UTF8 -NoNewline

Write-Host ''
Write-Host '[OK] 已按本机路径更新 cordis.patch.yml' -ForegroundColor Green
Write-Host "     python : $Python"
Write-Host "     repo   : $repo"
Write-Host ''
Write-Host '接下来:' -ForegroundColor Cyan
Write-Host "  1) dsh plugin --profile $Profile add file:$($repo -replace '\\','/')"
Write-Host "     (或从 GitHub 装: dsh plugin --profile $Profile add github:iii993/computer-use-advance)"
Write-Host '  2) 把 computer-use-advance 加进 profile 的 package.json -> dsh.profile.bundles'
Write-Host '  3) 重启 DSH(改动 profile 配置也会触发 cordis HMR 热加载)'