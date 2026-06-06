@echo off
chcp 65001 >nul
echo ============================================
echo  HLW8032 电力监测分析工具 - 打包脚本
echo ============================================
echo.

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

REM 检查 PyInstaller
python -c "import PyInstaller" >nul 2>&1
if %errorlevel% neq 0 (
    echo [信息] 正在安装 PyInstaller...
    pip install pyinstaller
)

REM 安装依赖
echo [信息] 安装依赖...
pip install -r requirements.txt

REM 清理旧构建
echo [信息] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "*.spec" del /q "*.spec"

REM PyInstaller 打包
echo [信息] 开始打包...
pyinstaller --onefile --windowed ^
    --name "HLW8032-Analyzer" ^
    --add-data "hlw8032_parser.py;." ^
    --add-data "device_analyzer.py;." ^
    --add-data "data_storage.py;." ^
    --add-data "msyh.ttc;." ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    --hidden-import serial.tools.list_ports ^
    main.py

if %errorlevel% neq 0 (
    echo [错误] 打包失败！
    pause
    exit /b 1
)

echo.
echo ============================================
echo  打包完成！
echo  输出文件: dist\HLW8032-Analyzer.exe
echo ============================================
pause
