@echo off
pushd "%~dp0"
python -m app_activity %*
set exit_code=%errorlevel%
popd
exit /b %exit_code%
