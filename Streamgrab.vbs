Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

scriptDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fileSystem.BuildPath(scriptDir, "app.py")
shell.CurrentDirectory = scriptDir
shell.Run "pyw -3 """ & scriptPath & """", 0, False
