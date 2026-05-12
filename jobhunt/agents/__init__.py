"""Agent implementations. Each agent is a subclass of BaseAgent.

The Orchestrator is in ``orchestrator.py``. Other agents live alongside
it and conform to the BaseAgent contract so the Orchestrator can invoke
them uniformly.
"""

from jobhunt.agents.base import AgentResult, BaseAgent
from jobhunt.agents.orchestrator import Orchestrator
from jobhunt.agents.strategy import StrategyAgent
from jobhunt.agents.discovery import DiscoveryAgent
from jobhunt.agents.vetting import VettingAgent
from jobhunt.agents.resume import ResumeArchitectAgent
from jobhunt.agents.submission import SubmissionAgent
from jobhunt.agents.tracking import TrackingAgent
from jobhunt.agents.improvement import ImprovementAgent
from jobhunt.agents.peer_critique import PeerCritiqueAgent

__all__ = [
    "AgentResult",
    "BaseAgent",
    "Orchestrator",
    "StrategyAgent",
    "DiscoveryAgent",
    "VettingAgent",
    "ResumeArchitectAgent",
    "SubmissionAgent",
    "TrackingAgent",
    "ImprovementAgent",
    "PeerCritiqueAgent",
]
