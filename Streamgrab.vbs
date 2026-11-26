Option Explicit

Dim shell, fileSystem, scriptDir, scriptPath, quotedPath
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

scriptDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fileSystem.BuildPath(scriptDir, "server.py")
quotedPath = Chr(34) & scriptPath & Chr(34)
shell.CurrentDirectory = scriptDir

' Prefer the Python launcher so the same Python selected by `py -3` is used.
On Error Resume Next
Err.Clear
shell.Run "pyw -3 " & quotedPath, 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run "pythonw " & quotedPath, 0, False
End If
If Err.Number <> 0 Then
    MsgBox "Streamgrab could not start. Install Python and make sure either pyw or pythonw is available.", _
           vbExclamation, "Streamgrab"
End If
On Error GoTo 0
