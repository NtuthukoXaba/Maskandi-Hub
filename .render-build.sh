#!/usr/bin/env bash

# Exit on error
set -o errexit

# Install dependencies
pip install -r requirements.txt

# Create necessary directories
mkdir -p static/uploads/artists
mkdir -p static/uploads/songs
mkdir -p static/uploads/votes
mkdir -p static/uploads/events
mkdir -p static/uploads/news

# Initialize the database
python -c "from app import init_db; init_db()"

echo "Build completed successfully"