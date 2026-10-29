#!/bin/bash
# Double-click to open the Job Automation Control Center (local web portal).
cd "$(dirname "$0")" && exec python3 portal/portal.py
