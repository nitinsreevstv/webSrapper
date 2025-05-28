#!/bin/bash
echo "✅ Starting Uvicorn on port $PORT"
uvicorn webscraper_api:app --host 0.0.0.0 --port $PORT

