#!/bin/bash
echo "✅ Starting Uvicorn on port ${PORT:-8000}"
uvicorn webscraper_api:app --host 0.0.0.0 --port ${PORT:-8000}
