#!/bin/bash
# deploy.sh - Deployment script for Raspberry Pi

set -e

echo "🚀 Deploying Power Flow Simulator to Raspberry Pi"

# Check if running on Raspberry Pi
if [[ $(uname -m) == "arm"* ]] || [[ $(uname -m) == "aarch64"* ]]; then
    echo "✅ Running on ARM architecture (Raspberry Pi detected)"
else
    echo "⚠️  Not running on ARM architecture - continuing anyway"
fi

# Update system packages
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install Docker if not already installed
if ! command -v docker &> /dev/null; then
    echo "🐳 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
    echo "✅ Docker installed. Please log out and back in to use Docker without sudo."
fi

# Install Docker Compose if not already installed
if ! command -v docker-compose &> /dev/null; then
    echo "🐳 Installing Docker Compose..."
    sudo apt install -y docker-compose
fi

# Create application directory
APP_DIR="/opt/powerflow-simulator"
echo "📁 Creating application directory: $APP_DIR"
sudo mkdir -p $APP_DIR
sudo chown $USER:$USER $APP_DIR

# Copy files to application directory
echo "📋 Copying application files..."
cp -r . $APP_DIR/

# Navigate to application directory
cd $APP_DIR

# Set executable permissions
chmod +x deploy.sh
chmod +x test_api.sh
chmod +x start.sh
chmod +x stop.sh

# Build Docker images
echo "🔨 Building Docker images..."
docker-compose build

# Start services
echo "🚀 Starting services..."
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 30

# Test the deployment
echo "🧪 Testing deployment..."
./test_api.sh

echo "✅ Deployment complete!"
echo "📊 API available at: http://$(hostname -I | awk '{print $1}'):8000"
echo "📈 Grafana dashboard: http://$(hostname -I | awk '{print $1}'):3000"

---
