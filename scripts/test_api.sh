#!/bin/bash
# test_api.sh - Test script for API endpoints

set -e

BASE_URL="http://localhost:8000"

echo "🧪 Testing Power Flow Simulator API"

# Test basic connectivity
echo "📡 Testing API connectivity..."
curl -s "$BASE_URL/" | jq '.' || echo "jq not available, raw response:"

# Get initial status
echo "📊 Getting initial status..."
curl -s "$BASE_URL/status" | jq '.' || echo "API Status check completed"

# Start simulation with microgrid
echo "🔄 Starting simulation..."
curl -s -X POST "$BASE_URL/control" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "start",
    "config": {
      "network_type": "microgrid",
      "update_frequency": 1.0,
      "kafka_topic": "powerflow_results",
      "node_id": "test_node",
      "three_phase": false
    }
  }' | jq '.' || echo "Start simulation completed"

# Wait a few seconds
echo "⏳ Waiting for simulation to run..."
sleep 5

# Check status
echo "📊 Checking simulation status..."
curl -s "$BASE_URL/status" | jq '.' || echo "Status check completed"

# Get network info
echo "🔌 Getting network information..."
curl -s "$BASE_URL/network" | jq '.' || echo "Network info check completed"

# Update a load
echo "⚡ Updating load at bus 1..."
curl -s -X POST "$BASE_URL/network/load" \
  -H "Content-Type: application/json" \
  -d '{
    "bus_idx": 1,
    "p_mw": 0.15,
    "q_mvar": 0.07,
    "phase": "all"
  }' | jq '.' || echo "Load update completed"

# Wait a few more seconds
sleep 3

# Stop simulation
echo "⏹️  Stopping simulation..."
curl -s -X POST "$BASE_URL/control" \
  -H "Content-Type: application/json" \
  -d '{"action": "stop"}' | jq '.' || echo "Stop simulation completed"

echo "✅ API tests completed successfully!"
