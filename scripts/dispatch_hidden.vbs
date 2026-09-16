' Launches dispatch_workflows.ps1 with no console window.
'
' Why this file exists. The scheduled task runs as the logged-on user
' ("Interactive only"), so Task Scheduler starts powershell.exe inside the
' user's session and Windows gives it a console. Every five minutes a terminal
' window opened on top of whatever the operator was doing, showed nothing --
' the script logs to a file, not the console -- and vanished. Reported
' 2026-09-16 as "windows terminal keeps popping up randomly then closing".
'
' -WindowStyle Hidden alone does NOT fix it: PowerShell applies that after it
' has started, so the console is created and then hidden, which is the flash.
' wscript.exe has no console of its own, and Run(..., 0, ...) starts the child
' hidden from the first instant, so no window is ever shown.
'
' Rejected: making the task "run whether the user is logged on or not", which
' would put it in session 0 with no window at all. Without a stored password
' that means an S4U logon, whose token is denied NETWORK access -- and this
' script's whole job is calling the GitHub API with the user's gh credentials.
' It would have traded a visible annoyance for a dispatcher that fails
' silently, which is the one failure mode this tripwire must not have.
'
' The third argument is TRUE (wait) on purpose. With False, wscript would exit
' immediately and the task would be "finished" while PowerShell ran on --
' defeating both -ExecutionTimeLimit (a hung `gh` would no longer be killed at
' 10 minutes) and -MultipleInstances IgnoreNew (instances would pile up every
' five minutes). Those two settings are the reason -Install exists; see the
' header of dispatch_workflows.ps1.

Option Explicit
Dim fso, shell, here, ps1, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = fso.BuildPath(here, "dispatch_workflows.ps1")

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """"

' 0 = hidden, True = wait for it to finish (see above).
WScript.Quit shell.Run(cmd, 0, True)
