#!/usr/bin/env bash
# One-time setup: find a working Python and create a venv
set -e

# Prefer Homebrew if available (faster/newer), fall back to Xcode CLT Python 3.9
if command -v brew &>/dev/null; then
  brew install python@3.11 --quiet 2>/dev/null || true
  PYTHON=$(brew --prefix python@3.11)/bin/python3.11
elif [ -x /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 ]; then
  PYTHON=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3
  echo "Using Xcode CLT Python 3.9"
else
  echo "No Python found. Install via: https://www.python.org/downloads/"
  exit 1
fi

echo "Using: $($PYTHON --version)"

if [ ! -d ".venv" ]; then
  $PYTHON -m venv .venv
fi

.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo ""
echo "Setup complete! Run with:"
echo "  source .venv/bin/activate && streamlit run app.py"
echo "  or: .venv/bin/streamlit run app.py"
