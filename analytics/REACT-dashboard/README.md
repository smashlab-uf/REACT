How to run

Open Terminal, go into this folder, then run:

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py

## Live backend setup

The feasibility dashboard reads from two live, read-only backend endpoints:

- `/dashboard/participants/`
- `/dashboard/latency-events/`

The API key must be provided through an environment variable. Do not place the key directly in the source code.

macOS/Linux:
export REACT_DASHBOARD_API_KEY="1de9479e8ba5ff3f7cff40de8af46e554b89aa43f8a9ed79f7d95dd7ba6598c4"
export REACT_USE_MOCK_DATA="false"

To run with live data:

export REACT_DASHBOARD_API_KEY="1de9479e8ba5ff3f7cff40de8af46e554b89aa43f8a9ed79f7d95dd7ba6598c4"
streamlit cache clear
streamlit run app.py

## Project structure

REACT-dashboard/
├── app.py
├── backend_client.py
├── requirements.txt
├── README.md
└── data/
    ├── decision_log.csv
    ├── decision_summary.csv
    └── mock_backend_health.json