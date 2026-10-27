# Starts the React (Vite) dev server on http://localhost:5173
# Run from the project root:   .\start-frontend.ps1
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\frontend"
if (-not (Test-Path ".\node_modules")) { npm install }
npm run dev
