@echo off
echo ============================================================
echo      AXIORA PULSE - AUTOMATED POSTGRESQL FIX
echo ============================================================
echo.

set HBA=C:\Program Files\PostgreSQL\18\data\pg_hba.conf

echo 1. Configuring PostgreSQL to Trust mode...
powershell -Command "(Get-Content '%HBA%') -replace 'scram-sha-256', 'trust' -replace 'md5', 'trust' | Set-Content '%HBA%'"

echo 2. Restarting PostgreSQL Service...
powershell -Command "Restart-Service postgresql-x64-18"
timeout /t 3 /nobreak > NUL

echo 3. Setting postgres password to 'postgres' and creating PulseDB...
"C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h 127.0.0.1 -c "ALTER USER postgres WITH PASSWORD 'postgres';"
"C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h 127.0.0.1 -c "CREATE DATABASE \"PulseDB\";"

echo 4. Reverting security settings...
powershell -Command "(Get-Content '%HBA%') -replace 'trust', 'scram-sha-256' | Set-Content '%HBA%'"

echo 5. Restarting PostgreSQL Service...
powershell -Command "Restart-Service postgresql-x64-18"

echo.
echo ============================================================
echo SUCCESS! PostgreSQL password is set to 'postgres' and PulseDB created.
echo ============================================================
pause
