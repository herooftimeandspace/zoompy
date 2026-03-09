# Zoompy (Python)

A Zoom API client built with Python using Test-Driven Development.

This repo starts with contract tests for the **Accounts** endpoints based on an OpenAPI schema
(`tests/schemas/Accounts.json`). Implementation comes next.

## Setup

Create a venv, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```