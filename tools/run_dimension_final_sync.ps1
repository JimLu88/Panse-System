$ErrorActionPreference = 'Stop'

$pythonExe = 'C:\Users\lzdwy\AppData\Local\Programs\Python\Python311\python.exe'
$publisher = 'D:\Panse-System\tools\publish_dimension_finals.py'

& $pythonExe $publisher
exit $LASTEXITCODE
