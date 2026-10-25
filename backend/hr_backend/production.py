"""uvicorn hr_backend.production:app --host 0.0.0.0 --port $PORT --workers 1"""
from .production_factory import create_production_app

app = create_production_app()
