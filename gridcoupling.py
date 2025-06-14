# grid_coupling.py - Refactored distributed grid coupling mechanism

import asyncio
import json
import logging
from typing import Dict, List, Optional, Union, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from abc import ABC, abstractmethod

import pandas as pd
import pandapower as pp
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
import numpy as np
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ============================================================================
# DATA MODELS FOR COUPLING
# ============================================================================

class BoundaryCondition(BaseModel):
    """Boundary conditions for grid coupling"""
    bus_id: int
    voltage_magnitude: float
    voltage_angle: float
    power_injection_real: float
    power_injection_imag: float
    timestamp: str
    source_node: str
    phase: Optional[str] = None  # For three-phase: 'a', 'b', 'c', or None for balanced

class ThreePhaseBoundaryCondition(BaseModel):
    """Three-phase boundary conditions"""
    bus_id: int
    vm_a_pu: float
    va_a_degree: float
    vm_b_pu: float
    va_b_degree: float
    vm_c_pu: float
    va_c_degree: float
    p_a_mw: float
    q_a_mvar: float
    p_b_mw: float
    q_b_mvar: float
    p_c_mw: float
    q_c_mvar: float
    timestamp: str
    source_node: str

class CouplingMessage(BaseModel):
    """Message format for grid coupling communication"""
    sender_node: str
    receiver_node: str
    three_phase_mode: bool
    boundary_conditions: Optional[List[BoundaryCondition]] = None
    three_phase_conditions: Optional[List[ThreePhaseBoundaryCondition]] = None
    timestamp: str
    iteration: int
    convergence_status: bool = False

class CouplingConfig(BaseModel):
    """Configuration for grid coupling"""
    node_id: str
    boundary_buses: List[int] = Field(default_factory=list)
    neighbor_nodes: List[str] = Field(default_factory=list)
    coupling_topic: str = "grid_coupling"
    max_iterations: int = Field(default=10, ge=1, le=50)
    convergence_tolerance: float = Field(default=1e-6, gt=0)
    kafka_timeout_ms: int = Field(default=1000, ge=100)
    enabled: bool = False

# ============================================================================
# BOUNDARY CONDITION EXTRACTORS
# ============================================================================

class BoundaryExtractor(ABC):
    """Abstract base class for boundary condition extraction"""
    
    @abstractmethod
    def extract_conditions(self, network: pp.pandapowerNet, 
                         boundary_buses: List[int], 
                         node_id: str) -> Union[List[BoundaryCondition], List[ThreePhaseBoundaryCondition]]:
        """Extract boundary conditions from network"""
        pass

class SinglePhaseBoundaryExtractor(BoundaryExtractor):
    """Extract single-phase boundary conditions"""
    
    def extract_conditions(self, network: pp.pandapowerNet, 
                         boundary_buses: List[int], 
                         node_id: str) -> List[BoundaryCondition]:
        """Extract single-phase boundary conditions"""
        conditions = []
        
        for bus_id in boundary_buses:
            if bus_id in network.res_bus.index:
                try:
                    vm_pu = float(network.res_bus.loc[bus_id, 'vm_pu'])
                    va_deg = float(network.res_bus.loc[bus_id, 'va_degree'])
                    p_mw = float(network.res_bus.loc[bus_id, 'p_mw'])
                    q_mvar = float(network.res_bus.loc[bus_id, 'q_mvar'])
                    
                    condition = BoundaryCondition(
                        bus_id=bus_id,
                        voltage_magnitude=vm_pu,
                        voltage_angle=va_deg,
                        power_injection_real=p_mw,
                        power_injection_imag=q_mvar,
                        timestamp=datetime.now().isoformat(),
                        source_node=node_id
                    )
                    conditions.append(condition)
                    
                except Exception as e:
                    logger.warning(f"Failed to extract boundary condition for bus {bus_id}: {e}")
        
        return conditions

class ThreePhaseBoundaryExtractor(BoundaryExtractor):
    """Extract three-phase boundary conditions"""
    
    def extract_conditions(self, network: pp.pandapowerNet, 
                         boundary_buses: List[int], 
                         node_id: str) -> List[ThreePhaseBoundaryCondition]:
        """Extract three-phase boundary conditions"""
        conditions = []
        
        if not hasattr(network, 'res_bus_3ph'):
            logger.warning("Three-phase results not available")
            return conditions
        
        for bus_id in boundary_buses:
            if bus_id in network.res_bus_3ph.index:
                try:
                    res = network.res_bus_3ph.loc[bus_id]
                    
                    condition = ThreePhaseBoundaryCondition(
                        bus_id=bus_id,
                        vm_a_pu=float(res['vm_a_pu']),
                        va_a_degree=float(res['va_a_degree']),
                        vm_b_pu=float(res['vm_b_pu']),
                        va_b_degree=float(res['va_b_degree']),
                        vm_c_pu=float(res['vm_c_pu']),
                        va_c_degree=float(res['va_c_degree']),
                        p_a_mw=float(res['p_a_mw']),
                        q_a_mvar=float(res['q_a_mvar']),
                        p_b_mw=float(res['p_b_mw']),
                        q_b_mvar=float(res['q_b_mvar']),
                        p_c_mw=float(res['p_c_mw']),
                        q_c_mvar=float(res['q_c_mvar']),
                        timestamp=datetime.now().isoformat(),
                        source_node=node_id
                    )
                    conditions.append(condition)
                    
                except Exception as e:
                    logger.warning(f"Failed to extract 3-phase boundary condition for bus {bus_id}: {e}")
        
        return conditions

# ============================================================================
# BOUNDARY CONDITION APPLICATORS
# ============================================================================

class BoundaryApplicator(ABC):
    """Abstract base class for applying boundary conditions"""
    
    @abstractmethod
    def apply_conditions(self, network: pp.pandapowerNet, 
                        conditions: Dict[str, List]) -> None:
        """Apply boundary conditions to network"""
        pass

class SinglePhaseBoundaryApplicator(BoundaryApplicator):
    """Apply single-phase boundary conditions"""
    
    def apply_conditions(self, network: pp.pandapowerNet, 
                        conditions: Dict[str, List[BoundaryCondition]]) -> None:
        """Apply single-phase boundary conditions"""
        for sender, cond_list in conditions.items():
            for condition in cond_list:
                bus_id = condition.bus_id
                
                if bus_id not in network.bus.index:
                    continue
                
                try:
                    self._apply_voltage_constraint(network, bus_id, condition)
                    self._apply_power_injection(network, bus_id, condition, sender)
                except Exception as e:
                    logger.warning(f"Failed to apply boundary condition for bus {bus_id}: {e}")
    
    def _apply_voltage_constraint(self, network: pp.pandapowerNet, 
                                bus_id: int, condition: BoundaryCondition):
        """Apply voltage constraint at boundary bus"""
        # Update external grid voltage if present
        ext_grid_mask = network.ext_grid.bus == bus_id
        if ext_grid_mask.any():
            ext_grid_idx = network.ext_grid[ext_grid_mask].index[0]
            network.ext_grid.loc[ext_grid_idx, 'vm_pu'] = condition.voltage_magnitude
    
    def _apply_power_injection(self, network: pp.pandapowerNet, 
                             bus_id: int, condition: BoundaryCondition, sender: str):
        """Apply power injection at boundary bus"""
        p_inj = condition.power_injection_real
        q_inj = condition.power_injection_imag
        
        if abs(p_inj) < 1e-6 and abs(q_inj) < 1e-6:
            return
        
        # Check for existing coupling load
        coupling_name = f"coupling_load_{sender}"
        coupling_mask = network.load.name == coupling_name
        
        if coupling_mask.any():
            # Update existing coupling load
            load_idx = network.load[coupling_mask].index[0]
            network.load.loc[load_idx, 'p_mw'] = -p_inj
            network.load.loc[load_idx, 'q_mvar'] = -q_inj
        else:
            # Create new coupling load
            pp.create_load(
                network, bus_id,
                p_mw=-p_inj,
                q_mvar=-q_inj,
                name=coupling_name
            )

class ThreePhaseBoundaryApplicator(BoundaryApplicator):
    """Apply three-phase boundary conditions"""
    
    def apply_conditions(self, network: pp.pandapowerNet, 
                        conditions: Dict[str, List[ThreePhaseBoundaryCondition]]) -> None:
        """Apply three-phase boundary conditions"""
        for sender, cond_list in conditions.items():
            for condition in cond_list:
                bus_id = condition.bus_id
                
                if bus_id not in network.bus.index:
                    continue
                
                try:
                    self._apply_voltage_constraints(network, bus_id, condition)
                    self._apply_power_injections(network, bus_id, condition, sender)
                except Exception as e:
                    logger.warning(f"Failed to apply 3-phase boundary condition for bus {bus_id}: {e}")
    
    def _apply_voltage_constraints(self, network: pp.pandapowerNet, 
                                 bus_id: int, condition: ThreePhaseBoundaryCondition):
        """Apply three-phase voltage constraints"""
        # This is a simplified approach - in practice, three-phase voltage
        # constraints are more complex and may require custom implementation
        ext_grid_mask = network.ext_grid.bus == bus_id
        if ext_grid_mask.any():
            # Use average voltage magnitude for external grid
            avg_vm = (condition.vm_a_pu + condition.vm_b_pu + condition.vm_c_pu) / 3
            ext_grid_idx = network.ext_grid[ext_grid_mask].index[0]
            network.ext_grid.loc[ext_grid_idx, 'vm_pu'] = avg_vm
    
    def _apply_power_injections(self, network: pp.pandapowerNet, 
                              bus_id: int, condition: ThreePhaseBoundaryCondition, sender: str):
        """Apply three-phase power injections"""
        coupling_name = f"coupling_3ph_{sender}"
        
        # Check for existing asymmetric coupling load
        if hasattr(network, 'asymmetric_load'):
            coupling_mask = network.asymmetric_load.name == coupling_name
            
            if coupling_mask.any():
                # Update existing coupling load
                load_idx = network.asymmetric_load[coupling_mask].index[0]
                network.asymmetric_load.loc[load_idx, 'p_a_mw'] = -condition.p_a_mw
                network.asymmetric_load.loc[load_idx, 'q_a_mvar'] = -condition.q_a_mvar
                network.asymmetric_load.loc[load_idx, 'p_b_mw'] = -condition.p_b_mw
                network.asymmetric_load.loc[load_idx, 'q_b_mvar'] = -condition.q_b_mvar
                network.asymmetric_load.loc[load_idx, 'p_c_mw'] = -condition.p_c_mw
                network.asymmetric_load.loc[load_idx, 'q_c_mvar'] = -condition.q_c_mvar
            else:
                # Create new asymmetric coupling load
                pp.create_asymmetric_load(
                    network, bus_id,
                    p_a_mw=-condition.p_a_mw, q_a_mvar=-condition.q_a_mvar,
                    p_b_mw=-condition.p_b_mw, q_b_mvar=-condition.q_b_mvar,
                    p_c_mw=-condition.p_c_mw, q_c_mvar=-condition.q_c_mvar,
                    name=coupling_name
                )

# ============================================================================
# CONVERGENCE CHECKER
# ============================================================================

class ConvergenceChecker:
    """Check convergence of coupling iterations"""
    
    def __init__(self, tolerance: float = 1e-6):
        self.tolerance = tolerance
        self.voltage_history = []
    
    def check_convergence(self, conditions: Union[List[BoundaryCondition], 
                                                List[ThreePhaseBoundaryCondition]]) -> bool:
        """Check if coupling has converged"""
        if len(self.voltage_history) < 2:
            current_voltages = self._extract_voltages(conditions)
            self.voltage_history.append(current_voltages)
            return False
        
        current_voltages = self._extract_voltages(conditions)
        previous_voltages = self.voltage_history[-1]
        
        if len(current_voltages) != len(previous_voltages):
            self.voltage_history.append(current_voltages)
            return False
        
        # Check convergence based on voltage differences
        voltage_diff = np.abs(current_voltages - previous_voltages)
        max_diff = np.max(voltage_diff) if len(voltage_diff) > 0 else 0
        
        converged = max_diff < self.tolerance
        logger.debug(f"Coupling convergence: max_diff={max_diff:.6f}, converged={converged}")
        
        self.voltage_history.append(current_voltages)
        
        return converged
    
    def _extract_voltages(self, conditions: Union[List[BoundaryCondition], 
                                                List[ThreePhaseBoundaryCondition]]) -> np.ndarray:
        """Extract voltage magnitudes for convergence checking"""
        if not conditions:
            return np.array([])
        
        if isinstance(conditions[0], BoundaryCondition):
            return np.array([c.voltage_magnitude for c in conditions])
        else:
            # For three-phase, use average voltage magnitude
            voltages = []
            for c in conditions:
                avg_vm = (c.vm_a_pu + c.vm_b_pu + c.vm_c_pu) / 3
                voltages.append(avg_vm)
            return np.array(voltages)
    
    def reset(self):
        """Reset convergence history"""
        self.voltage_history = []

# ============================================================================
# MAIN COUPLING MANAGER
# ============================================================================

class GridCouplingManager:
    """Manages distributed power flow coupling between nodes"""
    
    def __init__(self, config: CouplingConfig):
        self.config = config
        self.producer: Optional[KafkaProducer] = None
        self.consumer: Optional[KafkaConsumer] = None
        
        # State tracking
        self.iteration_count = 0
        self.convergence_checker = ConvergenceChecker(config.convergence_tolerance)
        
        # Strategy pattern for different phase modes
        self.single_phase_extractor = SinglePhaseBoundaryExtractor()
        self.three_phase_extractor = ThreePhaseBoundaryExtractor()
        self.single_phase_applicator = SinglePhaseBoundaryApplicator()
        self.three_phase_applicator = ThreePhaseBoundaryApplicator()
    
    def initialize_kafka(self, bootstrap_servers: str = "localhost:9092"):
        """Initialize Kafka producer and consumer"""
        try:
            self.producer = KafkaProducer(
                bootstrap_servers=[bootstrap_servers],
                value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8'),
                key_serializer=lambda k: k.encode('utf-8'),
                retries=3,
                retry_backoff_ms=100
            )
            
            self.consumer = KafkaConsumer(
                self.config.coupling_topic,
                bootstrap_servers=[bootstrap_servers],
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                consumer_timeout_ms=self.config.kafka_timeout_ms,
                group_id=f"coupling_{self.config.node_id}",
                auto_offset_reset='latest'
            )
            
            logger.info(f"Coupling Kafka initialized for node {self.config.node_id}")
            
        except Exception as e:
            logger.error(f"Failed to initialize coupling Kafka: {e}")
            raise
    
    def extract_boundary_conditions(self, network: pp.pandapowerNet, 
                                   three_phase_mode: bool) -> Union[List[BoundaryCondition], 
                                                                  List[ThreePhaseBoundaryCondition]]:
        """Extract boundary conditions based on mode"""
        if three_phase_mode:
            return self.three_phase_extractor.extract_conditions(
                network, self.config.boundary_buses, self.config.node_id
            )
        else:
            return self.single_phase_extractor.extract_conditions(
                network, self.config.boundary_buses, self.config.node_id
            )
    
    def send_boundary_conditions(self, conditions: Union[List[BoundaryCondition], 
                                                       List[ThreePhaseBoundaryCondition]], 
                               three_phase_mode: bool):
        """Send boundary conditions to neighboring nodes"""
        if not self.producer or not self.config.enabled:
            return
        
        for neighbor in self.config.neighbor_nodes:
            try:
                if three_phase_mode:
                    message = CouplingMessage(
                        sender_node=self.config.node_id,
                        receiver_node=neighbor,
                        three_phase_mode=True,
                        three_phase_conditions=conditions,
                        timestamp=datetime.now().isoformat(),
                        iteration=self.iteration_count
                    )
                else:
                    message = CouplingMessage(
                        sender_node=self.config.node_id,
                        receiver_node=neighbor,
                        three_phase_mode=False,
                        boundary_conditions=conditions,
                        timestamp=datetime.now().isoformat(),
                        iteration=self.iteration_count
                    )
                
                self.producer.send(
                    self.config.coupling_topic,
                    key=neighbor,
                    value=message.model_dump()
                )
                logger.debug(f"Sent coupling data to {neighbor}")
                
            except Exception as e:
                logger.error(f"Failed to send coupling data to {neighbor}: {e}")
    
    def receive_boundary_conditions(self, three_phase_mode: bool) -> Dict[str, List]:
        """Receive boundary conditions from neighboring nodes"""
        if not self.consumer or not self.config.enabled:
            return {}
        
        received = {}
        
        try:
            message_batch = self.consumer.poll(timeout_ms=self.config.kafka_timeout_ms)
            
            for topic_partition, messages in message_batch.items():
                for message in messages:
                    try:
                        data = message.value
                        coupling_msg = CouplingMessage(**data)
                        
                        # Only process messages for this node and matching phase mode
                        if (coupling_msg.receiver_node == self.config.node_id and 
                            coupling_msg.three_phase_mode == three_phase_mode):
                            
                            sender = coupling_msg.sender_node
                            
                            if three_phase_mode and coupling_msg.three_phase_conditions:
                                conditions = [ThreePhaseBoundaryCondition(**cond) 
                                            for cond in coupling_msg.three_phase_conditions]
                                received[sender] = conditions
                            elif not three_phase_mode and coupling_msg.boundary_conditions:
                                conditions = [BoundaryCondition(**cond) 
                                            for cond in coupling_msg.boundary_conditions]
                                received[sender] = conditions
                            
                            logger.debug(f"Received coupling data from {sender}")
                            
                    except Exception as e:
                        logger.warning(f"Failed to parse coupling message: {e}")
        
        except Exception as e:
            logger.error(f"Error receiving coupling data: {e}")
        
        return received
    
    def apply_boundary_conditions(self, network: pp.pandapowerNet, 
                                conditions: Dict[str, List], 
                                three_phase_mode: bool):
        """Apply boundary conditions to network"""
        if three_phase_mode:
            self.three_phase_applicator.apply_conditions(network, conditions)
        else:
            self.single_phase_applicator.apply_conditions(network, conditions)
    
    async def run_coupled_power_flow(self, power_flow_engine, three_phase_mode: bool) -> bool:
        """Run coupled power flow with neighboring nodes"""
        if not self.config.enabled or not self.config.boundary_buses:
            # Run normal power flow if coupling is disabled
            result = power_flow_engine.run_power_flow()
            return result['convergence']
        
        self.iteration_count = 0
        self.convergence_checker.reset()
        
        logger.info(f"Starting coupled power flow for node {self.config.node_id}")
        
        for iteration in range(self.config.max_iterations):
            self.iteration_count = iteration
            
            # Step 1: Run local power flow
            try:
                result = power_flow_engine.run_power_flow()
                if not result['convergence']:
                    logger.warning(f"Local power flow did not converge at iteration {iteration}")
                    continue
            except Exception as e:
                logger.error(f"Power flow failed at iteration {iteration}: {e}")
                continue
            
            # Step 2: Extract boundary conditions
            boundary_conditions = self.extract_boundary_conditions(
                power_flow_engine.network, three_phase_mode
            )
            
            # Step 3: Check coupling convergence
            if iteration > 0 and self.convergence_checker.check_convergence(boundary_conditions):
                logger.info(f"Coupled power flow converged in {iteration} iterations")
                return True
            
            # Step 4: Send boundary conditions to neighbors
            self.send_boundary_conditions(boundary_conditions, three_phase_mode)
            
            # Step 5: Receive boundary conditions from neighbors
            received_conditions = self.receive_boundary_conditions(three_phase_mode)
            
            # Step 6: Apply received boundary conditions
            if received_conditions:
                self.apply_boundary_conditions(
                    power_flow_engine.network, received_conditions, three_phase_mode
                )
                logger.debug(f"Applied boundary conditions from {len(received_conditions)} neighbors")
            
            # Small delay between iterations
            await asyncio.sleep(0.1)
        
        logger.warning(f"Coupled power flow did not converge in {self.config.max_iterations} iterations")
        return False
    
    def enable_coupling(self):
        """Enable distributed coupling"""
        self.config.enabled = True
        logger.info(f"Coupling enabled for node {self.config.node_id}")
    
    def disable_coupling(self):
        """Disable distributed coupling"""
        self.config.enabled = False
        logger.info(f"Coupling disabled for node {self.config.node_id}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current coupling status"""
        return {
            "node_id": self.config.node_id,
            "enabled": self.config.enabled,
            "boundary_buses": self.config.boundary_buses,
            "neighbor_nodes": self.config.neighbor_nodes,
            "iteration_count": self.iteration_count,
            "max_iterations": self.config.max_iterations,
            "convergence_tolerance": self.config.convergence_tolerance,
            "voltage_history_length": len(self.convergence_checker.voltage_history)
        }
    
    def update_config(self, new_config: CouplingConfig):
        """Update coupling configuration"""
        old_enabled = self.config.enabled
        self.config = new_config
        
        # Reset convergence checker if tolerance changed
        if self.convergence_checker.tolerance != new_config.convergence_tolerance:
            self.convergence_checker = ConvergenceChecker(new_config.convergence_tolerance)
        
        if old_enabled != new_config.enabled:
            if new_config.enabled:
                self.enable_coupling()
            else:
                self.disable_coupling()

# ============================================================================
# INTEGRATION WITH MAIN SIMULATOR
# ============================================================================

class CoupledPowerFlowEngine:
    """Power flow engine with coupling capabilities"""
    
    def __init__(self, base_engine, coupling_manager: GridCouplingManager):
        self.base_engine = base_engine
        self.coupling_manager = coupling_manager
    
    @property
    def network(self):
        """Access to underlying network"""
        return self.base_engine.network
    
    @property
    def three_phase_mode(self):
        """Access to phase mode"""
        return self.base_engine.three_phase_mode
    
    def set_network(self, network: pp.pandapowerNet, three_phase_mode: bool = False):
        """Set network for both base engine and coupling"""
        self.base_engine.set_network(network, three_phase_mode)
    
    async def run_power_flow(self) -> Dict[str, Any]:
        """Run power flow with optional coupling"""
        if self.coupling_manager.config.enabled:
            # Run coupled power flow
            converged = await self.coupling_manager.run_coupled_power_flow(
                self.base_engine, self.base_engine.three_phase_mode
            )
            
            # Get results from base engine
            try:
                result = self.base_engine.run_power_flow()
                result['convergence'] = converged
                result['coupling_info'] = self.coupling_manager.get_status()
                return result
            except Exception as e:
                logger.error(f"Failed to get results after coupled power flow: {e}")
                return {
                    'bus_results': {}, 'line_results': {}, 'gen_results': {}, 'load_results': {},
                    'convergence': converged, 'iteration_count': 0,
                    'coupling_info': self.coupling_manager.get_status()
                }
        else:
            # Run normal power flow
            result = self.base_engine.run_power_flow()
            result['coupling_info'] = {'enabled': False}
            return result

# Example usage and factory function
def create_coupled_simulator(base_simulator, coupling_config_dict: Dict[str, Any]):
    """Factory function to create coupled simulator"""
    coupling_config = CouplingConfig(**coupling_config_dict)
    coupling_manager = GridCouplingManager(coupling_config)
    
    # Wrap the base engine with coupling capabilities
    base_simulator.engine = CoupledPowerFlowEngine(
        base_simulator.engine, 
        coupling_manager
    )
    
    return base_simulator, coupling_manager

if __name__ == "__main__":
    # Example configuration
    coupling_config = {
        'node_id': 'pi_node_1',
        'boundary_buses': [0, 3],
        'neighbor_nodes': ['pi_node_2', 'pi_node_3'],
        'coupling_topic': 'grid_coupling',
        'max_iterations': 10,
        'convergence_tolerance': 1e-6,
        'enabled': False
    }
    
    print("Refactored grid coupling module loaded successfully!")
    print(f"Configuration: {coupling_config}")
