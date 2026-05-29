Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")
scriptDir = filesystem.GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & scriptDir & "\start_auto_mouse_windows.bat" & """", 0, False
