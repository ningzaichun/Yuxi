"""Fixed Schedule audit rules."""

from .contract import audit_contract
from .dependency import audit_dependencies
from .inactive import audit_inactive_tasks
from .management import audit_management
from .network import audit_network
from .resource import audit_resources

RULES = (
    audit_contract,
    audit_network,
    audit_dependencies,
    audit_inactive_tasks,
    audit_resources,
    audit_management,
)

__all__ = ["RULES"]
