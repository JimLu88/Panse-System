$ErrorActionPreference = 'Stop'

$pythonExe = 'C:\Users\lzdwy\AppData\Local\Programs\Python\Python311\python.exe'
$publisher = 'D:\AI\畔色ERP系统\ERP程序\tools\publish_dimension_finals.py'

& $pythonExe $publisher
exit $LASTEXITCODE
