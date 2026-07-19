"""Football AI Engine public API."""

from .engine import FootballAIEngine
from .ai_review import AI_REVIEW_PROMPT_VERSION, FAEAIReviewAnalyzer
from .daily_analysis import (
    DAILY_PROMPT_VERSION,
    FAEDailyAIAnalyzer,
    build_daily_match_input,
)
from .daily_review import (
    FAEDailyAIReviewEngine,
    aggregate_daily_ai_reviews,
    summarize_ai_settled,
)
from .draw_review import FAEDrawReviewEngine, aggregate_draw_reviews
from .learning import FAEReviewEngine
from .parlay import build_draw_parlays
from .provider import (
    ArkNarrativeClient,
    FAEConfigurationError,
    FAEError,
    FAEOutputError,
    FAEProviderError,
)
from .review_memory import REVIEW_MEMORY_VERSION, build_review_memory
from .skills import (
    SKILL_DEFINITIONS,
    SKILL_SCHEMA_VERSION,
    baseline_skill_documents,
    build_draw_skill_candidate,
    build_rule_skill_candidate,
)
from .version import ENGINE_CODE, ENGINE_NAME, ENGINE_VERSION, VERSION_MANIFEST

__all__ = [
    "ArkNarrativeClient",
    "AI_REVIEW_PROMPT_VERSION",
    "FAEConfigurationError",
    "FAEError",
    "FAEOutputError",
    "FAEProviderError",
    "REVIEW_MEMORY_VERSION",
    "FAEReviewEngine",
    "FAEDrawReviewEngine",
    "FAEDailyAIAnalyzer",
    "FAEDailyAIReviewEngine",
    "FAEAIReviewAnalyzer",
    "FootballAIEngine",
    "DAILY_PROMPT_VERSION",
    "build_daily_match_input",
    "build_review_memory",
    "aggregate_daily_ai_reviews",
    "summarize_ai_settled",
    "SKILL_DEFINITIONS",
    "SKILL_SCHEMA_VERSION",
    "baseline_skill_documents",
    "build_draw_skill_candidate",
    "build_rule_skill_candidate",
    "build_draw_parlays",
    "aggregate_draw_reviews",
    "ENGINE_CODE",
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "VERSION_MANIFEST",
]
