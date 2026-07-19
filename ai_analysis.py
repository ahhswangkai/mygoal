"""Backward-compatible imports for Football AI Engine.

New code should import from ``football_ai``. The legacy module name remains so
existing deployments and API imports continue to start during the migration.
"""

from football_ai import (
    ArkNarrativeClient,
    FAEConfigurationError,
    FAEError,
    FAEOutputError,
    FAEProviderError,
    FootballAIEngine,
)

AIAnalysisError = FAEError
AIConfigurationError = FAEConfigurationError
AIOutputError = FAEOutputError
AIProviderError = FAEProviderError
AIAnalysisService = FootballAIEngine
ArkResponsesClient = ArkNarrativeClient

__all__ = [
    "AIAnalysisError",
    "AIAnalysisService",
    "AIConfigurationError",
    "AIOutputError",
    "AIProviderError",
    "ArkResponsesClient",
]
