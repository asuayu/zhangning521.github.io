@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 打开 http://127.0.0.1:18720
python -m http.server 18720 --bind 127.0.0.1
