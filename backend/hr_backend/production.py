"""uvicorn hr_backend.production:app --host 0.0.0.0 --port $PORT --workers 1"""
from .production_startup import create_rolling_production_app

app = create_rolling_production_app()
