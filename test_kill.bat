@echo off
FOR /F "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do echo KILLING PID %%a
FOR /F "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do taskkill /f /pid %%a
