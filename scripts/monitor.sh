#!/bin/bash
# monitor.sh - Monitor system resources and simulation health

echo "📊 Power Flow Simulator Monitor"
echo "================================"

while true; do
    clear
    echo "📊 Power Flow Simulator Monitor - $(date)"
    echo "================================"
    
    # System resources
    echo "💻 System Resources:"
    echo "CPU Usage: $(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | awk -F'%' '{print $1}')"
    echo "Memory: $(free -h | awk '/^Mem:/ {print $3 "/" $2}')"
    echo "Temperature: $(vcgencmd measure_temp 2>/dev/null | cut -d= -f2 || echo "N/A")"
    echo ""
    
    # Docker container status
    echo "🐳 Container Status:"
    docker-compose ps
    echo ""
    
    # API health check
    echo "🔍 API Health:"
    if curl -s http://localhost:8000/status > /dev/null; then
        echo "✅ API responding"
        # Get simulation status
        STATUS=$(curl -s http://localhost:8000/status | jq -r '.running')
        PAUSED=$(curl -s http://localhost:8000/status | jq -r '.paused')
        ERRORS=$(curl -s http://localhost:8000/status | jq -r '.error_count')
        
        echo "Running: $STATUS"
        echo "Paused: $PAUSED"
        echo "Errors: $ERRORS"
    else
        echo "❌ API not responding"
    fi
    echo ""
    
    # Kafka topics (if available)
    echo "📨 Kafka Topics:"
    if docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list 2>/dev/null; then
        echo "✅ Kafka responding"
    else
        echo "❌ Kafka not available"
    fi
    echo ""
    
    echo "Press Ctrl+C to exit"
    sleep 10
done

---
