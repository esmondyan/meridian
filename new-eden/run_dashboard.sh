#!/bin/bash
cd /home/hermes/projects/meridian/new-eden
source venv/bin/activate
PYTHONUNBUFFERED=1 exec streamlit run dashboard_eve.py --server.port=8899
