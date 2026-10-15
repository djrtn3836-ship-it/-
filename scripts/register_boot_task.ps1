# stock_analyzer 부팅 자동 시작 작업 등록 (관리자 권한 필요)
#   사용법: 시작 버튼 우클릭 → "터미널(관리자)" 실행 후 아래 한 줄 붙여넣기
#   powershell -ExecutionPolicy Bypass -File "C:/Users/hdw38/Desktop/stock_analyzer/scripts/register_boot_task.ps1"
$ErrorActionPreference = 'Stop'
$root = 'C:/Users/hdw38/Desktop/stock_analyzer'
$action = New-ScheduledTaskAction -Execute 'C:/Windows/System32/wscript.exe' -Argument ('//B "' + $root + '/run_app_hidden.vbs"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'stock_analyzer_boot' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host ''
Write-Host 'OK - 부팅 자동 시작 등록 완료 (stock_analyzer_boot)'
Write-Host '  이제 PC가 재부팅되어도 로그인 없이(부팅 40초 후) 자동 시작됩니다.'
Write-Host ''
Read-Host '엔터를 누르면 닫힙니다'
