from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any


class NetworkSimulator:
    def __init__(self):
        self.state = {
            'router-mum-04': {'cpu': 45, 'latency_ms': 48, 'packet_loss_pct': 0.2, 'throughput_mbps': 410, 'healthy': True},
            'router-che-02': {'cpu': 34, 'latency_ms': 55, 'packet_loss_pct': 0.2, 'throughput_mbps': 410, 'healthy': True},
            'api-mum-01': {'cpu': 42, 'error_rate_pct': 0.4, 'latency_ms': 70, 'healthy': True},
            'dns-mum-01': {'cpu': 48, 'dns_latency_ms': 180, 'error_rate_pct': 0.5, 'healthy': True},
            'db-mum-01': {'cpu': 77, 'query_latency_ms': 210, 'healthy': True},
        }
        self.active_scenario: str | None = None
        self.fixed_layers: set[str] = set()
        self.history: list[dict[str, Any]] = []
        self._record()

    def trigger(self, scenario: str) -> dict[str, Any]:
        self.reset()
        self.active_scenario = scenario
        if scenario == 'router-overload':
            self.state['router-mum-04'].update(cpu=96, latency_ms=420, packet_loss_pct=18, throughput_mbps=980)
            self.state['api-mum-01'].update(latency_ms=420, error_rate_pct=7.2)
        elif scenario == 'dns-failure':
            self.state['dns-mum-01'].update(cpu=92, dns_latency_ms=1450, error_rate_pct=34, healthy=False)
            self.state['api-mum-01'].update(latency_ms=600, error_rate_pct=21)
        elif scenario == 'database-latency':
            self.state['db-mum-01'].update(cpu=94, query_latency_ms=1250)
            self.state['api-mum-01'].update(latency_ms=980, error_rate_pct=14)
        elif scenario == 'service-crash':
            self.state['api-mum-01'].update(cpu=0, latency_ms=3000, error_rate_pct=100, healthy=False)
        elif scenario == 'congestion':
            self.state['router-mum-04'].update(cpu=88, latency_ms=360, packet_loss_pct=8, throughput_mbps=1000)
            self.state['api-mum-01'].update(latency_ms=360, error_rate_pct=5.0)
        elif scenario == 'lb-imbalance':
            self.state['api-mum-01'].update(cpu=95, latency_ms=760, error_rate_pct=10)
        elif scenario == 'auth-degradation':
            self.state['api-mum-01'].update(latency_ms=700, error_rate_pct=25)
        elif scenario == 'complex':
            self.state['router-mum-04'].update(cpu=92, latency_ms=260, packet_loss_pct=4, throughput_mbps=990)
            self.state['db-mum-01'].update(cpu=89, query_latency_ms=680)
            self.state['api-mum-01'].update(latency_ms=680, error_rate_pct=12)
        else:
            raise ValueError(f'unknown scenario: {scenario}')
        self._record()
        return self.snapshot()

    def reset(self):
        self.__init__()

    # Which (action, target) pairs actually address each fault. Anything else executes but leaves the fault in place,
    # so validation can tell a correct fix from a wrong one.
    EFFECTIVE_FIXES = {
        'router-overload': {('redistribute_traffic', 'router-mum-04')},
        'congestion': {('redistribute_traffic', 'router-mum-04')},
        'dns-failure': {('restart_dns', 'dns-mum-01')},
        'database-latency': {('scale_service', 'db-mum-01')},
        'service-crash': {('restart_service', 'api-mum-01')},
        'lb-imbalance': {('scale_service', 'api-mum-01')},
        'auth-degradation': {('scale_service', 'api-mum-01')},
        'complex': {('redistribute_traffic', 'router-mum-04'), ('scale_service', 'db-mum-01')},
    }

    def remediate(self, action: str, target: str) -> dict[str, Any]:
        scenario = self.active_scenario
        if scenario and (action, target) not in self.EFFECTIVE_FIXES.get(scenario, set()):
            self._record()
            return {'success': True, 'effective': False, 'message': f'{action} executed on {target}; no effect on the active fault'}
        if scenario == 'complex':
            # Cascading failure: each correct action fixes its own layer; the API recovers only when both are fixed.
            self.fixed_layers.add(action)
            if action == 'redistribute_traffic':
                self.state['router-mum-04'].update(cpu=54, latency_ms=85, packet_loss_pct=0.7, throughput_mbps=650)
            else:
                self.state['db-mum-01'].update(cpu=58, query_latency_ms=120)
            if len(self.fixed_layers) == 2:
                self.state['api-mum-01'].update(latency_ms=95, error_rate_pct=0.8)
            self._record(); return {'success': True, 'effective': True, 'message': f'{action} applied ({len(self.fixed_layers)}/2 layers fixed)'}
        if action == 'redistribute_traffic' and target == 'router-mum-04':
            self.state['router-mum-04'].update(cpu=54, latency_ms=85, packet_loss_pct=0.7, throughput_mbps=650)
            self.state['api-mum-01'].update(latency_ms=92, error_rate_pct=0.8)
            self._record(); return {'success': True, 'message': 'Traffic redistributed successfully'}
        if action == 'restart_service' and target == 'api-mum-01':
            self.state['api-mum-01'].update(cpu=35, latency_ms=80, error_rate_pct=0.4, healthy=True)
            self._record(); return {'success': True, 'message': 'Service restarted successfully'}
        if action == 'restart_dns' and target == 'dns-mum-01':
            self.state['dns-mum-01'].update(cpu=30, dns_latency_ms=25, error_rate_pct=0.1, healthy=True)
            self.state['api-mum-01'].update(latency_ms=80, error_rate_pct=0.4)
            self._record(); return {'success': True, 'message': 'DNS service restored'}
        if action == 'scale_service' and target == 'api-mum-01':
            self.state['api-mum-01'].update(cpu=55, latency_ms=120, error_rate_pct=1.0)
            self._record(); return {'success': True, 'message': 'Service scaled'}
        if action == 'scale_service' and target == 'db-mum-01':
            self.state['db-mum-01'].update(cpu=58, query_latency_ms=120)
            self.state['api-mum-01'].update(latency_ms=120, error_rate_pct=1.0)
            self._record(); return {'success': True, 'message': 'Database capacity scaled'}
        return {'success': False, 'message': f'No simulator action for {action} on {target}'}

    def _record(self):
        m = self.state
        self.history.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'router_cpu': m['router-mum-04']['cpu'],
            'router_latency_ms': m['router-mum-04']['latency_ms'],
            'packet_loss_pct': m['router-mum-04']['packet_loss_pct'],
            'api_latency_ms': m['api-mum-01']['latency_ms'],
            'api_error_rate_pct': m['api-mum-01']['error_rate_pct'],
            'dns_latency_ms': m['dns-mum-01']['dns_latency_ms'],
            'db_query_latency_ms': m['db-mum-01']['query_latency_ms'],
        })
        self.history = self.history[-60:]

    def snapshot(self) -> dict[str, Any]:
        return {'scenario': self.active_scenario, 'metrics': self.state.copy(), 'history': list(self.history)}


simulator = NetworkSimulator()


def get_network_metrics(device: str | None = None) -> dict[str, Any]:
    if device and device in simulator.state:
        return {'device': device, 'metrics': {device: simulator.state[device]}, 'history': simulator.history[-5:]}
    result: dict[str, Any] = {'device': None, 'metrics': simulator.state, 'history': simulator.history[-5:]}
    if device:
        result['note'] = f'unknown device {device!r}; returning all devices. Known devices: {sorted(simulator.state)}'
    return result


def get_service_health(service: str = 'API Gateway') -> dict[str, Any]:
    m = simulator.state['api-mum-01']
    return {'service': service, 'healthy': bool(m.get('healthy', True)), 'metrics': m}


def query_logs(service: str = 'API Gateway', region: str = 'Mumbai') -> dict[str, Any]:
    scenario = simulator.active_scenario
    logs = {
        'router-overload': ['WARN router-mum-04 CPU threshold exceeded', 'WARN packet loss above 15%', 'INFO traffic queue depth rising'],
        'dns-failure': ['ERROR dns-mum-01 timeout rate exceeded', 'ERROR recursive resolver unavailable'],
        'database-latency': ['WARN db-mum-01 query latency above SLO', 'WARN connection pool saturation'],
        'service-crash': ['ERROR api-mum-01 process exited', 'ERROR health check failed'],
        'congestion': ['WARN interface saturation detected', 'WARN packet queue depth elevated'],
        'lb-imbalance': ['WARN api-mum-01 backend receives disproportionate traffic'],
        'auth-degradation': ['ERROR authentication dependency latency elevated'],
        'complex': ['WARN router saturation', 'WARN database latency increased'],
    }.get(scenario, ['INFO no matching active fault logs'])
    return {'service': service, 'region': region, 'logs': logs}


def get_topology(component: str = 'API Gateway') -> dict[str, Any]:
    graph = {
        'API Gateway': ['Load Balancer', 'Authentication', 'Billing DB'],
        'Load Balancer': ['router-mum-04', 'router-che-02'],
        'Authentication': ['auth-mum-01', 'db-mum-01'],
        'Billing DB': ['db-mum-01'],
    }
    return {'component': component, 'dependencies': graph.get(component, [])}


def check_dependencies(service: str = 'API Gateway') -> dict[str, Any]:
    return {'service': service, 'dependencies': {
        'network': 'degraded' if simulator.active_scenario in {'router-overload','congestion','complex'} else 'healthy',
        'database': 'degraded' if simulator.active_scenario in {'database-latency','complex'} else 'healthy',
        'dns': 'degraded' if simulator.active_scenario == 'dns-failure' else 'healthy',
    }}


def restart_service(target: str) -> dict[str, Any]: return simulator.remediate('restart_service', target)
def redistribute_traffic(target: str) -> dict[str, Any]: return simulator.remediate('redistribute_traffic', target)
def scale_service(target: str) -> dict[str, Any]: return simulator.remediate('scale_service', target)
def restart_dns(target: str = 'dns-mum-01') -> dict[str, Any]: return simulator.remediate('restart_dns', target)
def create_ticket(target: str = 'NOC') -> dict[str, Any]: return {'success': True, 'ticket_id': f'INC-TICKET-{random.randint(10000, 99999)}', 'target': target, 'message': 'Escalation ticket created'}


def validate_remediation(target: str = 'API Gateway') -> dict[str, Any]:
    scenario = simulator.active_scenario
    router = simulator.state['router-mum-04']
    api = simulator.state['api-mum-01']
    dns = simulator.state['dns-mum-01']
    db = simulator.state['db-mum-01']
    checks = {}
    if scenario == 'complex':
        checks = {
            'packet_loss_under_2': router.get('packet_loss_pct', 999) < 2,
            'db_query_latency_under_200': db.get('query_latency_ms', 9999) < 200,
            'api_latency_under_150': api.get('latency_ms', 9999) < 150,
        }
    elif scenario in {'router-overload', 'congestion'} or target == 'router-mum-04':
        checks = {
            'router_cpu_under_70': router.get('cpu', 999) < 70,
            'packet_loss_under_2': router.get('packet_loss_pct', 999) < 2,
            'api_latency_under_150': api.get('latency_ms', 9999) < 150,
        }
    elif target == 'dns-mum-01' or scenario == 'dns-failure':
        checks = {
            'dns_healthy': dns.get('healthy', False),
            'dns_latency_under_300': dns.get('dns_latency_ms', 9999) < 300,
            'api_latency_under_150': api.get('latency_ms', 9999) < 150,
        }
    elif target == 'db-mum-01' or scenario in {'database-latency'}:
        checks = {
            'db_query_latency_under_200': db.get('query_latency_ms', 9999) < 200,
            'api_latency_under_150': api.get('latency_ms', 9999) < 150,
        }
    else:
        checks = {
            'api_healthy': api.get('healthy', False),
            'api_latency_under_150': api.get('latency_ms', 9999) < 150,
            'api_errors_under_2': api.get('error_rate_pct', 9999) < 2,
        }
    return {'target': target, 'success': all(checks.values()), 'checks': checks, 'metrics': {'router': router, 'api': api, 'dns': dns, 'db': db}}
