' Запускает run_bot.bat без окна терминала.
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run Chr(34) & folder & "\run_bot.bat" & Chr(34), 0, False
