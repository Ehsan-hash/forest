"""Select explicit task execution isolation while retaining existing local work."""
from research.agents.processes import ManagedProcesses


def process_manager(workspace, config=None):
    backend = (config or {}).get('execution_backend', 'local')
    if backend == 'container':
        from runners.container import ContainerProcesses
        return ContainerProcesses(workspace, (config or {}).get('container', {}))
    if backend == 'bubblewrap':
        from runners.bubblewrap import BubblewrapProcesses
        return BubblewrapProcesses(workspace, config)
    if backend != 'local':
        raise ValueError('execution_backend must be local, container or bubblewrap')
    return ManagedProcesses(workspace,code_inputs=(config or {}).get('code_inputs'))
