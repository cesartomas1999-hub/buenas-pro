' Lanza actualizar.ps1 sin abrir ninguna ventana (la usa el Programador de tareas).
Dim shell, codigo
Set shell = CreateObject("WScript.Shell")
codigo = shell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""D:\buenas-pro\actualizar.ps1""", 0, True)
WScript.Quit codigo
