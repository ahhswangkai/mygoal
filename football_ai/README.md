# Football AI Engine (FAE)

FAE replaces the old prompt-file Skill chain with a versioned analysis engine. The
deterministic core owns market classification, scores, probabilities,
recommendations and risk control. The daily Ark layer makes one auditable primary
selection per match; a consistency guard overrides only severe contradictions
against the deterministic probabilities and prediction-time odds.

## Runtime pipeline

1. `FootballAIEngine.build_context()` normalizes mygoal odds and any available
   fundamentals (form, H2H, standings, schedule, injuries, lineups and weather).
2. The engine classifies the market into A-G types and evaluates rule signals.
3. Eight dimensions are scored with explicit versioned weights.
4. Outcome, winning-margin, official-handicap and total-goals probabilities are
   calculated.
5. A recommendation and risk profile are selected in a fixed schema.
6. Ark writes a five-market judgement and primary selection for each match, then
   performs a separate whole-day ranking and combination synthesis.
7. `FAEDailyAIReviewEngine` settles the immutable all-pre-match run, including
   singles, 2-leg/3-leg combinations, odds, returns and guardrail conflicts.
8. Ark performs a cached post-match diagnosis whenever the settled-result
   snapshot changes. It records per-match causes, market lessons and bounded
   weighting proposals, but cannot mutate production Skill parameters.
9. Earlier deep reviews are distilled into date-isolated memory. Recent
   observations are non-binding; a proposed pattern becomes validated prompt
   guidance only after recurring across days with enough match evidence.
10. Rule hit rates are persisted and grouped into versioned Skill candidates after
   the minimum total and new-sample thresholds.
11. Candidates are replayed against historical reviews, then explicitly promoted
   or rolled back; production parameters never change merely because one match
   finished.

## Modules

- `engine.py`: data normalization, A-G classification, scoring, probabilities,
  recommendations and risk control.
- `learning.py`: post-match grading and per-rule evaluation.
- `daily_analysis.py`: per-match Ark judgements, cross-match synthesis and
  consistency guardrails.
- `daily_review.py`: immutable daily-AI settlement and aggregate ROI statistics.
- `ai_review.py`: cached Ark post-match diagnosis and candidate-only learning
  proposals.
- `review_memory.py`: future-safe review memory, recurrence validation and prompt
  feedback policy.
- `provider.py`: optional Volcengine Ark narrative client.
- `skills.py`: Skill definitions, candidate construction and replay validation.
- `version.py`: engine version, dimension weights, rule defaults and learning
  policy.

## Persistence

- `fae_analyses`: latest analysis for each match.
- `fae_analysis_history`: immutable analysis history when the input hash changes.
- `fae_reviews`: post-match reviews.
- `fae_rule_weights`: samples, hits, accuracy and learned weights.
- `fae_versions`: version manifests and feature history.
- `fae_draw_snapshots`: immutable pre-match draw/handicap-draw plans.
- `fae_draw_reviews`: daily single, 2-leg and 3-leg settlement results.
- `fae_draw_strategy_weights`: automatically learned draw strategy weights.
- `fae_daily_ai_runs`: whole-day AI summaries and immutable run metadata.
- `fae_daily_ai_matches`: one prediction-time odds snapshot per run and match.
- `fae_daily_ai_batches`: paid Ark checkpoints used for safe retries.
- `fae_daily_ai_reviews`: AI-primary settlement plus cached Ark deep review.
- `fae_skill_versions`: immutable active and historical Skill versions.
- `fae_skill_candidates`: review-generated candidates waiting for promotion.
- `fae_skill_deployments`: promotion and rollback audit history.

The old `ai_analyses` collection is read-only fallback data so existing analyses
remain visible during migration.

## API

- `GET /api/match/<id>/fae-analysis`
- `POST /api/match/<id>/fae-analysis`
- `GET /api/match/<id>/fae-review`
- `GET /api/fae/rankings?date=YYYY-MM-DD`
- `GET /api/fae/draw-parlays?date=YYYY-MM-DD`
- `GET /api/fae/draw-review?date=YYYY-MM-DD`
- `GET /api/fae/draw-review/stats`
- `POST /api/fae/draw-review`
- `GET /api/fae/daily-ai?date=YYYY-MM-DD`
- `POST /api/fae/daily-ai`
- `GET /api/fae/daily-ai/match/<id>`
- `GET /api/fae/daily-ai/review?date=YYYY-MM-DD`
- `GET /api/fae/daily-ai/review/stats`
- `POST /api/fae/daily-ai/review`
- `GET /api/fae/version`
- `POST /api/fae/analyze-daily`
- `POST /api/fae/review`
- `GET /api/fae/skills`
- `POST /api/fae/skills/candidates`
- `POST /api/fae/skills/<skill_id>/promote`
- `POST /api/fae/skills/<skill_id>/rollback`

The former `/api/match/<id>/ai-analysis` route remains an alias for compatibility.

## Version policy

- FAE v1.0: market analysis.
- FAE v1.5: review and learning.
- FAE v2.0: introduced the fundamental-data interface.
- FAE v2.1: versioned Skill candidates, replay validation, controlled promotion
  and rollback.
- FAE v3.0: intended milestone for calibrated historical accuracy and richer
  automatic weighting after enough reviewed samples exist.
