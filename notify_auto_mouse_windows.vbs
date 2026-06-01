Set shell = CreateObject("WScript.Shell")

message = "Auto Mouse Move status changed."
If WScript.Arguments.Count > 0 Then
    message = WScript.Arguments(0)
End If

shell.Popup message, 3, "Auto Mouse Move", 64
