#!/bin/bash
# start.sh - Start the power flow simulator

echo "🚀 Starting Power Flow Simulator..."

# Start Docker Compose services
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 15

# Check service health
echo "🔍 Checking service health..."
docker-compose ps

# Display URLs
echo "✅ Services started!"
echo "📊 API: http://localhost:8000"
echo "📈 API Docs: http://localhost:8000/docs"
echo "📋 Status: http://localhost:8000/status"
