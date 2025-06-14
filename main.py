# Real-time Power Flow Simulator
# Refactored modular application combining pandapower, FastAPI, and Kafka

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from abc import ABC, abstractmethod

import pandas as pd
import pandapower as pp
import pandapower.networks as pn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn
from kafka import KafkaProducer
from kafka.errors import KafkaError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# DATA MODELS
# ============================================================================

class SimulationConfig(BaseModel):
    """Configuration for power flow simulation"""
    network_type: str = Field(default="microgrid", description="Type of network to simulate")
    update_frequency: float = Field(default=1.0, ge=0.1, le=10.0, description="Update frequency in Hz")
    kafka_topic: str = Field(default="powerflow_results", description="Kafka topic for results")
    node_id: str = Field(default="pi_node_1", description="Unique node identifier")
    coupling_buses: Optional[List[int]] = Field(default=None, description="Buses for grid coupling")
    three_phase: bool = Field(default=False, description="Enable three-phase unbalanced analysis")

class PowerFlowResult(BaseModel):
    """Power flow calculation results"""
    timestamp: str
    node_id: str
    bus_results: Dict[str, Any]
    line_results: Dict[str, Any]
    gen_results: Dict[str, Any]
    load_results: Dict[str, Any]
    convergence: bool
    iteration_count: int
    three_phase_mode: bool

class SimulationControl(BaseModel):
    """Control commands for simulation"""
    action: str = Field(..., regex="^(start|stop|pause|resume)$")
    config: Optional[SimulationConfig] = None

class LoadUpdate(BaseModel):
    """Load update parameters"""
    bus_idx: int = Field(..., ge=0, description="Bus index")
    p_mw: float = Field(..., description="Active power in MW")
    q_mvar: Optional[float] = Field(default=None, description="Reactive power in MVAr")
    phase: str = Field(default="all", regex="^(all|a|b|c)$", description="Phase for asymmetric loads")

class PhaseModeRequest(BaseModel):
    """Request to switch phase mode"""
    three_phase: bool = Field(..., description="Enable three-phase mode")

@dataclass
class SimulationState:
    """Internal simulation state"""
    running: bool = False
    paused: bool = False
    config: Optional[SimulationConfig] = None
    last_update: Optional[datetime] = None
    error_count: int = 0

# ============================================================================
# NETWORK BUILDERS
# ============================================================================

class NetworkBuilder(ABC):
    """Abstract base class for network builders"""
    
    @abstractmethod
    def build_network(self, three_phase: bool = False) -> pp.pandapowerNet:
        """Build and return a pandapower network"""
        pass

class MicrogridBuilder(NetworkBuilder):
    """Builder for microgrid networks"""
    
    def build_network(self, three_phase: bool = False) -> pp.pandapowerNet:
        """Create a simple microgrid for demonstration"""
        net = pp.create_empty_network(name="Microgrid")
        
        # Create buses
        buses = self._create_buses(net, three_phase)
        
        # Create connections
        self._create_connections(net, buses, three_phase)
        
        # Create grid connection
        self._create_grid_connection(net, buses)
        
        # Create loads and generators
        self._create_loads(net, buses, three_phase)
        self._create_generators(net, buses, three_phase)
        
        return net
    
    def _create_buses(self, net: pp.pandapowerNet, three_phase: bool) -> Dict[str, int]:
        """Create network buses"""
        buses = {
            'hv': pp.create_bus(net, vn_kv=20.0, name="HV Bus"),
            'lv1': pp.create_bus(net, vn_kv=0.4, name="LV Bus 1"),
            'lv2': pp.create_bus(net, vn_kv=0.4, name="LV Bus 2"),
            'solar': pp.create_bus(net, vn_kv=0.4, name="Solar Bus")
        }
        
        if three_phase:
            buses['unbalanced'] = pp.create_bus(net, vn_kv=0.4, name="Unbalanced Load Bus")
        
        return buses
    
    def _create_connections(self, net: pp.pandapowerNet, buses: Dict[str, int], three_phase: bool):
        """Create transformers and lines"""
        # Transformer
        pp.create_transformer(net, buses['hv'], buses['lv1'], std_type="0.25 MVA 20/0.4 kV")
        
        # Lines
        pp.create_line(net, buses['lv1'], buses['lv2'], length_km=0.5, std_type="NAYY 4x50 SE")
        pp.create_line(net, buses['lv2'], buses['solar'], length_km=0.3, std_type="NAYY 4x50 SE")
        
        if three_phase:
            pp.create_line(net, buses['lv2'], buses['unbalanced'], length_km=0.2, std_type="NAYY 4x50 SE")
    
    def _create_grid_connection(self, net: pp.pandapowerNet, buses: Dict[str, int]):
        """Create external grid connection"""
        pp.create_ext_grid(net, buses['hv'], vm_pu=1.02, name="Grid Connection")
    
    def _create_loads(self, net: pp.pandapowerNet, buses: Dict[str, int], three_phase: bool):
        """Create loads"""
        pp.create_load(net, buses['lv1'], p_mw=0.1, q_mvar=0.05, name="Load 1")
        pp.create_load(net, buses['lv2'], p_mw=0.08, q_mvar=0.04, name="Load 2")
        
        if three_phase and 'unbalanced' in buses:
            pp.create_asymmetric_load(
                net, buses['unbalanced'],
                p_a_mw=0.05, q_a_mvar=0.02,
                p_b_mw=0.03, q_b_mvar=0.015,
                p_c_mw=0.07, q_c_mvar=0.035,
                name="Unbalanced Load"
            )
    
    def _create_generators(self, net: pp.pandapowerNet, buses: Dict[str, int], three_phase: bool):
        """Create generators"""
        if three_phase:
            pp.create_asymmetric_sgen(
                net, buses['solar'],
                p_a_mw=0.02, q_a_mvar=0,
                p_b_mw=0.015, q_b_mvar=0,
                p_c_mw=0.025, q_c_mvar=0,
                name="3-Phase Solar PV"
            )
        else:
            pp.create_sgen(net, buses['solar'], p_mw=0.05, q_mvar=0, name="Solar PV")

class NetworkFactory:
    """Factory for creating networks"""
    
    _builders = {
        'microgrid': MicrogridBuilder(),
    }
    
    @classmethod
    def create_network(cls, network_type: str, three_phase: bool = False) -> pp.pandapowerNet:
        """Create network based on type"""
        if network_type in cls._builders:
            return cls._builders[network_type].build_network(three_phase)
        elif network_type == "simple_four_bus":
            return pn.simple_four_bus_system()
        elif network_type == "ieee_case":
            return pn.case9()
        else:
            raise ValueError(f"Unknown network type: {network_type}")

# ============================================================================
# POWER FLOW ENGINE
# ============================================================================

class PowerFlowEngine:
    """Handles power flow calculations"""
    
    def __init__(self):
        self.network: Optional[pp.pandapowerNet] = None
        self.three_phase_mode: bool = False
    
    def set_network(self, network: pp.pandapowerNet, three_phase_mode: bool = False):
        """Set the network for power flow calculations"""
        self.network = network
        self.three_phase_mode = three_phase_mode
        logger.info(f"Network set: {three_phase_mode and 'three-phase' or 'single-phase'} mode")
    
    def run_power_flow(self) -> Dict[str, Any]:
        """Execute power flow calculation"""
        if self.network is None:
            raise RuntimeError("No network set")
        
        try:
            # Run appropriate power flow
            if self.three_phase_mode:
                pp.runpp_3ph(self.network, algorithm='nr', calculate_voltage_angles=True)
                logger.debug("Executed three-phase power flow")
            else:
                pp.runpp(self.network, algorithm='nr', calculate_voltage_angles=True)
                logger.debug("Executed single-phase power flow")
            
            # Extract and return results
            return {
                'bus_results': self._extract_bus_results(),
                'line_results': self._extract_line_results(),
                'gen_results': self._extract_gen_results(),
                'load_results': self._extract_load_results(),
                'convergence': self.network.converged,
                'iteration_count': getattr(self.network, '_ppc', {}).get('iterations', 0)
            }
            
        except Exception as e:
            logger.error(f"Power flow calculation failed: {e}")
            raise
    
    def _extract_bus_results(self) -> Dict[str, Any]:
        """Extract bus results based on mode"""
        if self.three_phase_mode:
            return self._extract_3ph_bus_results()
        else:
            return self._extract_1ph_bus_results()
    
    def _extract_1ph_bus_results(self) -> Dict[str, Any]:
        """Extract single-phase bus results"""
        return {
            'vm_pu': self.network.res_bus.vm_pu.to_dict(),
            'va_degree': self.network.res_bus.va_degree.to_dict(),
            'p_mw': self.network.res_bus.p_mw.to_dict(),
            'q_mvar': self.network.res_bus.q_mvar.to_dict()
        }
    
    def _extract_3ph_bus_results(self) -> Dict[str, Any]:
        """Extract three-phase bus results"""
        if not hasattr(self.network, 'res_bus_3ph'):
            return {}
        
        res = self.network.res_bus_3ph
        return {
            # Phase A
            'vm_a_pu': res.vm_a_pu.to_dict(),
            'va_a_degree': res.va_a_degree.to_dict(),
            'p_a_mw': res.p_a_mw.to_dict(),
            'q_a_mvar': res.q_a_mvar.to_dict(),
            # Phase B
            'vm_b_pu': res.vm_b_pu.to_dict(),
            'va_b_degree': res.va_b_degree.to_dict(),
            'p_b_mw': res.p_b_mw.to_dict(),
            'q_b_mvar': res.q_b_mvar.to_dict(),
            # Phase C
            'vm_c_pu': res.vm_c_pu.to_dict(),
            'va_c_degree': res.va_c_degree.to_dict(),
            'p_c_mw': res.p_c_mw.to_dict(),
            'q_c_mvar': res.q_c_mvar.to_dict(),
            # Unbalance
            'unbalance_degree': self._calculate_voltage_unbalance().to_dict()
        }
    
    def _extract_line_results(self) -> Dict[str, Any]:
        """Extract line results based on mode"""
        if self.three_phase_mode:
            return self._extract_3ph_line_results()
        else:
            return self._extract_1ph_line_results()
    
    def _extract_1ph_line_results(self) -> Dict[str, Any]:
        """Extract single-phase line results"""
        return {
            'p_from_mw': self.network.res_line.p_from_mw.to_dict(),
            'q_from_mvar': self.network.res_line.q_from_mvar.to_dict(),
            'p_to_mw': self.network.res_line.p_to_mw.to_dict(),
            'q_to_mvar': self.network.res_line.q_to_mvar.to_dict(),
            'pl_mw': self.network.res_line.pl_mw.to_dict(),
            'loading_percent': self.network.res_line.loading_percent.to_dict()
        }
    
    def _extract_3ph_line_results(self) -> Dict[str, Any]:
        """Extract three-phase line results"""
        if not hasattr(self.network, 'res_line_3ph'):
            return {}
        
        res = self.network.res_line_3ph
        return {
            # Phase flows
            'p_a_from_mw': res.p_a_from_mw.to_dict(),
            'q_a_from_mvar': res.q_a_from_mvar.to_dict(),
            'p_b_from_mw': res.p_b_from_mw.to_dict(),
            'q_b_from_mvar': res.q_b_from_mvar.to_dict(),
            'p_c_from_mw': res.p_c_from_mw.to_dict(),
            'q_c_from_mvar': res.q_c_from_mvar.to_dict(),
            # Total losses and loading
            'pl_mw': res.pl_mw.to_dict(),
            'loading_percent': res.loading_percent.to_dict()
        }
    
    def _extract_gen_results(self) -> Dict[str, Any]:
        """Extract generator results"""
        if len(self.network.res_gen) == 0:
            return {}
        
        return {
            'p_mw': self.network.res_gen.p_mw.to_dict(),
            'q_mvar': self.network.res_gen.q_mvar.to_dict()
        }
    
    def _extract_load_results(self) -> Dict[str, Any]:
        """Extract load results based on mode"""
        if self.three_phase_mode:
            return {
                'symmetric': {
                    'p_mw': self.network.res_load.p_mw.to_dict() if len(self.network.res_load) > 0 else {},
                    'q_mvar': self.network.res_load.q_mvar.to_dict() if len(self.network.res_load) > 0 else {}
                },
                'asymmetric': self._extract_asymmetric_load_results()
            }
        else:
            return {
                'p_mw': self.network.res_load.p_mw.to_dict(),
                'q_mvar': self.network.res_load.q_mvar.to_dict()
            }
    
    def _extract_asymmetric_load_results(self) -> Dict[str, Any]:
        """Extract asymmetric load results"""
        if not hasattr(self.network, 'res_asymmetric_load') or len(self.network.res_asymmetric_load) == 0:
            return {}
        
        res = self.network.res_asymmetric_load
        return {
            'p_a_mw': res.p_a_mw.to_dict(),
            'q_a_mvar': res.q_a_mvar.to_dict(),
            'p_b_mw': res.p_b_mw.to_dict(),
            'q_b_mvar': res.q_b_mvar.to_dict(),
            'p_c_mw': res.p_c_mw.to_dict(),
            'q_c_mvar': res.q_c_mvar.to_dict()
        }
    
    def _calculate_voltage_unbalance(self) -> pd.Series:
        """Calculate voltage unbalance factor"""
        try:
            if not hasattr(self.network, 'res_bus_3ph'):
                return pd.Series(dtype=float)
            
            res = self.network.res_bus_3ph
            vm_avg = (res.vm_a_pu + res.vm_b_pu + res.vm_c_pu) / 3
            
            max_dev = pd.concat([
                abs(res.vm_a_pu - vm_avg),
                abs(res.vm_b_pu - vm_avg),
                abs(res.vm_c_pu - vm_avg)
            ], axis=1).max(axis=1)
            
            return (max_dev / vm_avg) * 100
            
        except Exception as e:
            logger.warning(f"Could not calculate voltage unbalance: {e}")
            return pd.Series(dtype=float)

# ============================================================================
# KAFKA STREAMING
# ============================================================================

class KafkaStreamer:
    """Handles Kafka message streaming"""
    
    def __init__(self):
        self.producer: Optional[KafkaProducer] = None
    
    def initialize(self, bootstrap_servers: str = "localhost:9092"):
        """Initialize Kafka producer"""
        try:
            self.producer = KafkaProducer(
                bootstrap_servers=[bootstrap_servers],
                value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8'),
                retries=3,
                retry_backoff_ms=100
            )
            logger.info("Kafka producer initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Kafka: {e}")
            raise
    
    def stream_result(self, result: PowerFlowResult):
        """Stream power flow result to Kafka"""
        if self.producer is None:
            return
        
        try:
            future = self.producer.send(
                'powerflow_results',  # Use a fixed topic name
                value=result.model_dump(),
                key=result.node_id.encode('utf-8')
            )
            future.add_errback(lambda e: logger.error(f"Kafka send failed: {e}"))
        except Exception as e:
            logger.error(f"Failed to stream to Kafka: {e}")

# ============================================================================
# MAIN SIMULATOR
# ============================================================================

class PowerFlowSimulator:
    """Main power flow simulator class"""
    
    def __init__(self):
        self.state = SimulationState()
        self.engine = PowerFlowEngine()
        self.streamer = KafkaStreamer()
        self.simulation_task: Optional[asyncio.Task] = None
        self.executor = ThreadPoolExecutor(max_workers=2)
    
    def initialize_kafka(self, bootstrap_servers: str = "localhost:9092"):
        """Initialize Kafka streaming"""
        self.streamer.initialize(bootstrap_servers)
    
    def create_network(self, network_type: str, three_phase: bool = False):
        """Create and set network"""
        network = NetworkFactory.create_network(network_type, three_phase)
        self.engine.set_network(network, three_phase)
        
        mode = "three-phase" if three_phase else "single-phase"
        logger.info(f"Network created: {network_type} ({mode})")
        self._log_network_info()
    
    def _log_network_info(self):
        """Log network information"""
        net = self.engine.network
        if net is None:
            return
        
        logger.info(f"Buses: {len(net.bus)}, Lines: {len(net.line)}")
        
        if self.engine.three_phase_mode:
            asym_loads = len(net.asymmetric_load) if hasattr(net, 'asymmetric_load') else 0
            asym_sgens = len(net.asymmetric_sgen) if hasattr(net, 'asymmetric_sgen') else 0
            logger.info(f"Asymmetric loads: {asym_loads}, Asymmetric generators: {asym_sgens}")
    
    async def simulation_loop(self):
        """Main simulation loop"""
        logger.info("Starting simulation loop")
        
        while self.state.running:
            if not self.state.paused:
                try:
                    # Run power flow calculation
                    loop = asyncio.get_event_loop()
                    pf_results = await loop.run_in_executor(
                        self.executor, self.engine.run_power_flow
                    )
                    
                    # Create result object
                    result = PowerFlowResult(
                        timestamp=datetime.now().isoformat(),
                        node_id=self.state.config.node_id,
                        three_phase_mode=self.engine.three_phase_mode,
                        **pf_results
                    )
                    
                    # Stream to Kafka
                    await loop.run_in_executor(
                        self.executor, self.streamer.stream_result, result
                    )
                    
                    self.state.last_update = datetime.now()
                    self.state.error_count = 0
                    
                    # Periodic logging
                    if int(time.time()) % 10 == 0:
                        logger.info(f"Simulation running - Convergence: {result.convergence}")
                
                except Exception as e:
                    logger.error(f"Simulation error: {e}")
                    self.state.error_count += 1
                    if self.state.error_count > 5:
                        logger.error("Too many errors, stopping simulation")
                        break
            
            # Wait for next iteration
            await asyncio.sleep(1.0 / self.state.config.update_frequency)
        
        logger.info("Simulation loop stopped")
    
    def start_simulation(self, config: SimulationConfig):
        """Start simulation"""
        if self.state.running:
            raise RuntimeError("Simulation already running")
        
        self.state.config = config
        self.create_network(config.network_type, config.three_phase)
        
        self.state.running = True
        self.state.paused = False
        self.state.error_count = 0
        
        self.simulation_task = asyncio.create_task(self.simulation_loop())
        logger.info("Simulation started")
    
    def stop_simulation(self):
        """Stop simulation"""
        self.state.running = False
        self.state.paused = False
        
        if self.simulation_task:
            self.simulation_task.cancel()
        
        logger.info("Simulation stopped")
    
    def pause_simulation(self):
        """Pause simulation"""
        if not self.state.running:
            raise RuntimeError("Simulation not running")
        
        self.state.paused = True
        logger.info("Simulation paused")
    
    def resume_simulation(self):
        """Resume simulation"""
        if not self.state.running:
            raise RuntimeError("Simulation not running")
        
        self.state.paused = False
        logger.info("Simulation resumed")
    
    def get_status(self) -> Dict[str, Any]:
        """Get simulation status"""
        network_info = {}
        if self.engine.network is not None:
            net = self.engine.network
            network_info = {
                "buses": len(net.bus),
                "lines": len(net.line),
                "loads": len(net.load),
                "asymmetric_loads": len(net.asymmetric_load) if hasattr(net, 'asymmetric_load') else 0,
                "three_phase_mode": self.engine.three_phase_mode
            }
        
        return {
            "running": self.state.running,
            "paused": self.state.paused,
            "last_update": self.state.last_update.isoformat() if self.state.last_update else None,
            "error_count": self.state.error_count,
            "config": self.state.config.model_dump() if self.state.config else None,
            "network_info": network_info
        }
    
    def get_network_info(self) -> Dict[str, Any]:
        """Get detailed network information"""
        if self.engine.network is None:
            raise RuntimeError("No network loaded")
        
        net = self.engine.network
        info = {
            "buses": len(net.bus),
            "lines": len(net.line),
            "loads": len(net.load),
            "generators": len(net.gen),
            "three_phase_mode": self.engine.three_phase_mode,
            "bus_details": net.bus.to_dict('records'),
            "line_details": net.line.to_dict('records')
        }
        
        # Add three-phase specific information
        if self.engine.three_phase_mode:
            info.update({
                "asymmetric_loads": len(net.asymmetric_load) if hasattr(net, 'asymmetric_load') else 0,
                "asymmetric_sgens": len(net.asymmetric_sgen) if hasattr(net, 'asymmetric_sgen') else 0,
            })
            
            if hasattr(net, 'asymmetric_load') and len(net.asymmetric_load) > 0:
                info["asymmetric_load_details"] = net.asymmetric_load.to_dict('records')
            
            if hasattr(net, 'asymmetric_sgen') and len(net.asymmetric_sgen) > 0:
                info["asymmetric_sgen_details"] = net.asymmetric_sgen.to_dict('records')
        
        return info
    
    def update_load(self, load_update: LoadUpdate):
        """Update load at specific bus"""
        if self.engine.network is None:
            raise RuntimeError("No network loaded")
        
        net = self.engine.network
        
        # Handle three-phase specific updates
        if self.engine.three_phase_mode and load_update.phase in ["a", "b", "c"]:
            if not hasattr(net, 'asymmetric_load'):
                raise ValueError("No asymmetric loads in current network")
            
            mask = net.asymmetric_load.bus == load_update.bus_idx
            if not mask.any():
                raise ValueError(f"No asymmetric load found at bus {load_update.bus_idx}")
            
            idx = net.asymmetric_load[mask].index[0]
            net.asymmetric_load.loc[idx, f'p_{load_update.phase}_mw'] = load_update.p_mw
            
            if load_update.q_mvar is not None:
                net.asymmetric_load.loc[idx, f'q_{load_update.phase}_mvar'] = load_update.q_mvar
            
            return f"Asymmetric load phase {load_update.phase.upper()} updated at bus {load_update.bus_idx}"
        
        else:
            # Update symmetric load
            mask = net.load.bus == load_update.bus_idx
            if not mask.any():
                raise ValueError(f"No load found at bus {load_update.bus_idx}")
            
            net.load.loc[mask, 'p_mw'] = load_update.p_mw
            if load_update.q_mvar is not None:
                net.load.loc[mask, 'q_mvar'] = load_update.q_mvar
            
            return f"Load updated at bus {load_update.bus_idx}"
    
    def switch_phase_mode(self, three_phase: bool):
        """Switch between single-phase and three-phase modes"""
        if self.state.running:
            raise RuntimeError("Cannot switch modes while simulation is running")
        
        if self.state.config is None:
            raise RuntimeError("No configuration available")
        
        # Update config and recreate network
        new_config = self.state.config.model_copy()
        new_config.three_phase = three_phase
        
        self.state.config = new_config
        self.create_network(new_config.network_type, new_config.three_phase)
        
        mode = "three-phase" if three_phase else "single-phase"
        return f"Successfully switched to {mode} mode"

# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

# Initialize FastAPI app and simulator
app = FastAPI(
    title="Power Flow Simulator API",
    version="2.0.0",
    description="Real-time distributed power flow simulation with Kafka streaming"
)
simulator = PowerFlowSimulator()

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    try:
        simulator.initialize_kafka()
    except Exception as e:
        logger.warning(f"Kafka initialization failed: {e}")

@app.get("/", tags=["General"])
async def root():
    """API root endpoint"""
    return {"message": "Power Flow Simulator API v2.0", "status": "running"}

@app.get("/status", tags=["Monitoring"])
async def get_status():
    """Get current simulation status"""
    return simulator.get_status()

@app.post("/control", tags=["Control"])
async def control_simulation(control: SimulationControl):
    """Control simulation (start/stop/pause/resume)"""
    try:
        if control.action == "start":
            if not control.config:
                raise HTTPException(status_code=400, detail="Config required for start")
            simulator.start_simulation(control.config)
        elif control.action == "stop":
            simulator.stop_simulation()
        elif control.action == "pause":
            simulator.pause_simulation()
        elif control.action == "resume":
            simulator.resume_simulation()
        
        return {"message": f"Simulation {control.action} successful"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/network", tags=["Network"])
async def get_network_info():
    """Get information about the current network"""
    try:
        return simulator.get_network_info()
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/network/load", tags=["Network"])
async def update_load(load_update: LoadUpdate):
    """Update load at specific bus"""
    try:
        message = simulator.update_load(load_update)
        return {"message": message}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/network/phase-mode", tags=["Network"])
async def switch_phase_mode(request: PhaseModeRequest):
    """Switch between single-phase and three-phase modes"""
    try:
        message = simulator.switch_phase_mode(request.three_phase)
        return {"message": message, "three_phase_mode": request.three_phase}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/coupling/status", tags=["Coupling"])
async def get_coupling_status():
    """Get coupling status if available"""
    try:
        if hasattr(simulator.engine, 'coupling_manager'):
            return simulator.engine.coupling_manager.get_status()
        else:
            return {"enabled": False, "message": "Coupling not configured"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/coupling/control", tags=["Coupling"])
async def control_coupling(action: str):
    """Enable or disable coupling"""
    try:
        if not hasattr(simulator.engine, 'coupling_manager'):
            raise HTTPException(status_code=400, detail="Coupling not configured")
        
        if action == "enable":
            simulator.engine.coupling_manager.enable_coupling()
            return {"message": "Coupling enabled"}
        elif action == "disable":
            simulator.engine.coupling_manager.disable_coupling()
            return {"message": "Coupling disabled"}
        else:
            raise HTTPException(status_code=400, detail="Invalid action. Use 'enable' or 'disable'")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import os
    
    # Configuration from environment variables
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    
    # Optional coupling configuration
    coupling_enabled = os.getenv("COUPLING_ENABLED", "false").lower() == "true"
    
    if coupling_enabled:
        # Import and configure coupling
        try:
            from grid_coupling import create_coupled_simulator, CouplingConfig
            
            coupling_config = {
                'node_id': os.getenv("NODE_ID", "pi_node_1"),
                'boundary_buses': [int(x) for x in os.getenv("BOUNDARY_BUSES", "0,3").split(",")],
                'neighbor_nodes': os.getenv("NEIGHBOR_NODES", "").split(",") if os.getenv("NEIGHBOR_NODES") else [],
                'coupling_topic': os.getenv("COUPLING_TOPIC", "grid_coupling"),
                'max_iterations': int(os.getenv("MAX_ITERATIONS", "10")),
                'convergence_tolerance': float(os.getenv("CONVERGENCE_TOLERANCE", "1e-6")),
                'enabled': True
            }
            
            simulator, coupling_manager = create_coupled_simulator(simulator, coupling_config)
            
            # Initialize coupling Kafka
            try:
                coupling_manager.initialize_kafka()
                logger.info("Coupling system initialized successfully")
            except Exception as e:
                logger.warning(f"Coupling Kafka initialization failed: {e}")
                
        except ImportError:
            logger.warning("Grid coupling module not available")
        except Exception as e:
            logger.error(f"Failed to initialize coupling: {e}")
    
    # Start the application
    logger.info(f"Starting Power Flow Simulator API v2.0 on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
