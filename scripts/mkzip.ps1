# Backward-compatible wrapper (2026-10-19): actual implementation is scripts/mkzip.py
# (PowerShell ZipFile has FileShare.Read limitations on files being written + an
#  unexplained NullReference in function context; Python zipfile reads shared files fine.)
python "$PSScriptRoot\mkzip.py"
exit $LASTEXITCODE
