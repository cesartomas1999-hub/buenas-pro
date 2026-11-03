# Descarga las buenas pro y las sube a GitHub. Lo ejecuta el Programador de tareas cada mañana.
# También puedes ejecutarlo a mano: clic derecho > "Ejecutar con PowerShell".

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$git = "C:\Program Files\Git\cmd\git.exe"
$log = Join-Path $PSScriptRoot "actualizar.log"
$env:PYTHONIOENCODING = "utf-8"

"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====" | Out-File $log -Append -Encoding utf8

# Evitar que dos ejecuciones corran a la vez (p. ej. al iniciar sesión y a las 6:00)
$candado = New-Object System.Threading.Mutex($false, "Global\BuenasProActualizar")
if (-not $candado.WaitOne(0)) { "Ya hay otra actualización en curso; salgo." | Out-File $log -Append -Encoding utf8; exit 0 }

# Esperar hasta 10 minutos a que haya internet (por si la PC recién se encendió)
for ($i = 0; $i -lt 20; $i++) {
    try { Invoke-WebRequest -UseBasicParsing "https://github.com" -TimeoutSec 15 | Out-Null; break }
    catch { Start-Sleep 30 }
}

& $git pull -q --rebase origin main 2>&1 | Out-File $log -Append -Encoding utf8
& $python descargar.py 2>&1 | Out-File $log -Append -Encoding utf8
if ($LASTEXITCODE -ne 0) { "ERROR en la descarga" | Out-File $log -Append -Encoding utf8; exit 1 }

& $git add data
& $git commit -q -m "Datos $(Get-Date -Format 'yyyy-MM-dd')" 2>&1 | Out-File $log -Append -Encoding utf8
& $git push -q origin main 2>&1 | Out-File $log -Append -Encoding utf8
"OK" | Out-File $log -Append -Encoding utf8
