@echo off
title ARF Development Server

echo ============================================
echo   ARF -- Agent Resource Framework
echo   Development Mode
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10+ is required but not found.
    echo Install from https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python found

REM Check Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js is required but not found.
    echo Install from https://nodejs.org/
    pause
    exit /b 1
)
echo [OK] Node.js found

REM Navigate to project root
cd /d "%~dp0.."

REM Install Python package
echo.
echo Installing Python package...
pip install -e . >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install Python package.
    pause
    exit /b 1
)
echo [OK] Python package installed

REM Install frontend dependencies
echo.
echo Installing frontend dependencies...
cd frontend
call npm install >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install frontend dependencies.
    cd ..
    pause
    exit /b 1
)
cd ..
echo [OK] Frontend dependencies installed

REM Create workspace if needed
if not exist "default_workspace\arf_agent.yaml" (
    echo.
    echo Creating default workspace...
    arf init default_workspace
    if errorlevel 1 (
        echo [ERROR] Failed to create workspace.
        pause
        exit /b 1
    )
) else (
    echo [OK] Workspace found
)

echo.
echo ============================================
echo   Starting ARF...
echo.
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:5173
echo.
echo   Press Ctrl+C to stop all services.
echo ============================================
echo.

arf start -w default_workspace

pause
