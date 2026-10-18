' Starts the PlayVault UI with no console window at all (shortcuts point here).
' All the logic is in playvault.ps1.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\playvault.ps1""", 0, False
