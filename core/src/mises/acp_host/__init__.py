"""Host external coding agents over the Agent Client Protocol (client side)."""
from .session import AcpSession, choose_option
from .specs import KNOWN_AGENTS, AcpAgentSpec

__all__ = ["KNOWN_AGENTS", "AcpAgentSpec", "AcpSession", "choose_option"]
