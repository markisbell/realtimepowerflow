#!/bin/bash
# stop.sh - Stop the power flow simulator

echo "⏹️  Stopping Power Flow Simulator..."

# Stop Docker Compose services
docker-compose down

echo "✅ Services stopped!"
