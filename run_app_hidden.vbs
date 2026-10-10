' stock_analyzer 자동 실행(창 숨김) — Windows 예약 작업용
' 콘솔 창을 닫아 앱이 종료되는 사고를 막기 위해 숨김 실행한다.
Dim shell, fso, base
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd /c """ & base & "\run_app_auto.bat""", 0, False
