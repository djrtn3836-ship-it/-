' supply_backfill 수급 백필(창 숨김) — 장시간 무인 실행용
' 콘솔 창을 닫아 백필이 중단되는 사고를 막기 위해 숨김 실행한다.
Dim shell, fso, base
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd /c """ & base & "\run_backfill_auto.bat""", 0, False
