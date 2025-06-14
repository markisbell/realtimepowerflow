# kafka_consumer.py - Test Kafka consumer to verify streaming

import json
import logging
from kafka import KafkaConsumer
from kafka.errors import KafkaError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """Test Kafka consumer for power flow results"""
    
    consumer = KafkaConsumer(
        'powerflow_results',
        bootstrap_servers=['localhost:9092'],
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        group_id='test_consumer',
        auto_offset_reset='latest'
    )
    
    print("🎧 Listening for power flow results on Kafka...")
    print("Press Ctrl+C to stop")
    
    try:
        for message in consumer:
            data = message.value
            print(f"\n📊 Received from {data['node_id']} at {data['timestamp']}")
            print(f"Convergence: {data['convergence']}")
            
            # Print bus voltages
            bus_voltages = data['bus_results']['vm_pu']
            print("Bus Voltages (p.u.):")
            for bus_id, voltage in bus_voltages.items():
                print(f"  Bus {bus_id}: {voltage:.4f}")
            
            # Print line flows if available
            if data['line_results']['p_from_mw']:
                line_flows = data['line_results']['p_from_mw']
                print("Line Flows (MW):")
                for line_id, flow in line_flows.items():
                    print(f"  Line {line_id}: {flow:.4f}")
            
            print("-" * 50)
    
    except KeyboardInterrupt:
        print("\n👋 Shutting down consumer...")
    except Exception as e:
        logger.error(f"Consumer error: {e}")
    finally:
        consumer.close()

if __name__ == "__main__":
    main()
