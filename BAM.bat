@echo off
setlocal enabledelayedexpansion
title BAM - Business Automation Machine
color 0A

:: Detect script location
set "BAM_DIR=%~dp0"
set "BAM_DIR=%BAM_DIR:~0,-1%"

:: Check for Python/uv
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] uv not found. Install: pip install uv
    pause
    exit /b 1
)

:: Check for bam module
if not exist "%BAM_DIR%\bam\cli.py" (
    echo [ERROR] bam\cli.py not found in %BAM_DIR%
    pause
    exit /b 1
)

:MENU
cls
echo ============================================================
echo   BAM - Business Automation Machine
echo   Version 0.1.0
echo ============================================================
echo.
echo   [1]  Health Check
echo   [2]  Research Lead
echo   [3]  List Leads
echo   [4]  Approve Lead
echo   [5]  Record Contact
echo   [6]  Deliver PDF to Excel
echo   [7]  Deliver Excel Cleaner
echo   [8]  List Jobs
echo   [9]  Record Payment
echo   [10] Business Digest
echo   [11] Backup Database
echo   [12] Open Data Folder
echo   [13] Next Action (bam next)
echo   [14] Pending Follow-ups
echo   [15] Sales Brief
echo   [16] Draft Outreach
echo   [17] Exit
echo.
echo ============================================================
set /p choice="Select option: "

if "%choice%"=="1" goto HEALTH
if "%choice%"=="2" goto RESEARCH
if "%choice%"=="3" goto LEADS
if "%choice%"=="4" goto APPROVE
if "%choice%"=="5" goto CONTACT
if "%choice%"=="6" goto DELIVER_PDF
if "%choice%"=="7" goto DELIVER_CLEAN
if "%choice%"=="8" goto JOBS
if "%choice%"=="9" goto PAY
if "%choice%"=="10" goto DIGEST
if "%choice%"=="11" goto BACKUP
if "%choice%"=="12" goto OPEN_DATA
if "%choice%"=="13" goto NEXT
if "%choice%"=="14" goto FOLLOWUPS
if "%choice%"=="15" goto SALESBRIEF
if "%choice%"=="16" goto DRAFTEMAIL
if "%choice%"=="17" goto EXIT

echo Invalid option.
pause
goto MENU

:HEALTH
cls
echo Running health check...
cd /d "%BAM_DIR%"
uv run bam doctor
echo.
echo Running services check...
uv run bam services
echo.
pause
goto MENU

:RESEARCH
cls
echo Research a new lead
echo.
set /p url="Enter URL: "
if "%url%"=="" goto MENU
cd /d "%BAM_DIR%"
uv run bam research "%url%"
echo.
pause
goto MENU

:LEADS
cls
cd /d "%BAM_DIR%"
uv run bam leads
echo.
pause
goto MENU

:APPROVE
cls
echo Approve a lead for contact
echo.
set /p lid="Enter lead ID: "
if "%lid%"=="" goto MENU
cd /d "%BAM_DIR%"
uv run bam approve %lid% -y
echo.
pause
goto MENU

:CONTACT
cls
echo Record contact outcome
echo.
set /p lid="Enter lead ID: "
if "%lid%"=="" goto MENU
set /p outcome="Enter outcome (contacted/replied/lost): "
if "%outcome%"=="" goto MENU
cd /d "%BAM_DIR%"
uv run bam contact %lid% %outcome%
echo.
pause
goto MENU

:DELIVER_PDF
cls
echo Deliver PDF to Excel
echo.
set /p file="Enter PDF file path: "
if "%file%"=="" goto MENU
set /p client="Enter client name (optional): "
set /p agreed="Enter agreed amount (optional): "
cd /d "%BAM_DIR%"
if "%client%"=="" (
    uv run bam deliver "%file%" --service pdf-to-excel --yes
) else (
    if "%agreed%"=="" (
        uv run bam deliver "%file%" --service pdf-to-excel --client "%client%" --yes
    ) else (
        uv run bam deliver "%file%" --service pdf-to-excel --client "%client%" --agreed %agreed% --yes
    )
)
echo.
pause
goto MENU

:DELIVER_CLEAN
cls
echo Deliver Excel Cleaner
echo.
set /p file="Enter CSV/Excel file or directory: "
if "%file%"=="" goto MENU
set /p client="Enter client name (optional): "
set /p agreed="Enter agreed amount (optional): "
cd /d "%BAM_DIR%"
if "%client%"=="" (
    uv run bam deliver "%file%" --service excel-cleaner --yes
) else (
    if "%agreed%"=="" (
        uv run bam deliver "%file%" --service excel-cleaner --client "%client%" --yes
    ) else (
        uv run bam deliver "%file%" --service excel-cleaner --client "%client%" --agreed %agreed% --yes
    )
)
echo.
pause
goto MENU

:JOBS
cls
cd /d "%BAM_DIR%"
uv run bam jobs
echo.
pause
goto MENU

:PAY
cls
echo Record payment
echo.
set /p jid="Enter job ID: "
if "%jid%"=="" goto MENU
set /p amt="Enter amount: "
if "%amt%"=="" goto MENU
cd /d "%BAM_DIR%"
uv run bam pay %jid% --amount %amt%
echo.
pause
goto MENU

:DIGEST
cls
cd /d "%BAM_DIR%"
uv run bam digest
echo.
pause
goto MENU

:BACKUP
cls
echo Creating database backup...
cd /d "%BAM_DIR%"
uv run bam backup
echo.
pause
goto MENU

:OPEN_DATA
explorer "%BAM_DIR%\data"
goto MENU

:NEXT
cls
cd /d "%BAM_DIR%"
uv run bam next
echo.
pause
goto MENU

:FOLLOWUPS
cls
cd /d "%BAM_DIR%"
uv run bam followups
echo.
pause
goto MENU

:SALESBRIEF
cls
echo Generate sales brief
echo.
set /p lid="Enter lead ID: "
if "%lid%"=="" goto MENU
cd /d "%BAM_DIR%"
uv run bam sales-brief %lid%
echo.
pause
goto MENU

:DRAFTEMAIL
cls
echo Draft outreach message
echo.
set /p lid="Enter lead ID: "
if "%lid%"=="" goto MENU
cd /d "%BAM_DIR%"
uv run bam draft-outreach %lid%
echo.
pause
goto MENU

:EXIT
exit /b 0
