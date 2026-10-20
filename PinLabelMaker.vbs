' Starts Pin Label Maker with no console window and opens it in the browser.
' Double-click this (or point a shortcut at it). Run StopPinLabelMaker.bat to shut it down.
Dim sh, fso, dir, q
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
q = Chr(34)
sh.CurrentDirectory = dir
' Full path on purpose: some Windows setups don't search the current directory.
sh.Run "cmd /c " & q & q & dir & "\PinLabelMaker.bat" & q & " hidden" & q, 0, False
