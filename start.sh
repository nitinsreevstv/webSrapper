#!/bin/bash
uvicorn webscraper_api:app --host 0.0.0.0 --port $PORT
