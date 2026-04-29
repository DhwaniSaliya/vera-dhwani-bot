# Vera Message Engine - Dhwani Bot

## Overview
This submission implements a deterministic message engine for Vera (magicpin's merchant growth assistant).  
The bot composes the next proactive action from structured context across:
- category
- merchant
- trigger
- optional customer

It returns grounded message actions with body, CTA, sender identity, suppression key, and rationale.

## Runtime Design
- **Stack:** FastAPI + Python rule engine
- **Core composer:** `engine.py` (`compose_action()`)
- **API service:** `main.py` (stateful in-memory context + tick/reply orchestration)
- **Determinism:** For the same stored context + trigger input, output remains stable.

## Composition Approach
The engine is trigger-first and context-grounded:
1. Resolve active trigger(s) from `/v1/tick`.
2. Join trigger -> merchant -> category (and optional customer).
3. Select message branch by `trigger.kind` (research, dip, spike, festival/event, recall/CRM, etc.).
4. Fill message slots only from provided JSON:
   - merchant identity/locality/performance/signals/offers
   - category digest/offer catalog/voice constraints
   - trigger payload, urgency, suppression key
5. Enforce constraints:
   - body length <= 320 chars
   - single CTA
   - no URL insertion
6. Return rationale explaining why this message was chosen now.

## Endpoint Contract
Implemented endpoints:
- `POST /v1/context`
- `POST /v1/tick`
- `POST /v1/reply`
- `GET /v1/healthz`
- `GET /v1/metadata`

### Context handling
- Idempotent version tracking per `(scope, context_id)`
- Stale versions rejected with conflict response
- Higher versions replace prior state atomically

### Tick behavior
- Consumes `available_triggers`
- Emits `actions` list with required fields (`conversation_id`, `merchant_id`, `trigger_id`, `body`, `cta`, `suppression_key`, `rationale`, etc.)
- Uses suppression tracking to reduce repeated sends for the same suppression key

### Reply behavior (replay-aware)
- Detects common auto-replies -> `wait`
- Handles hostile/opt-out intent -> `end`
- Handles commitment ("let's do it", "what's next") -> concrete `send`
- Handles budget/time objections with low-friction continuation

## Why deterministic rules (model choice)
Runtime message generation does not use an LLM.  
This is intentional to maximize:
- reproducibility under replay
- strict grounding to context JSON
- predictable behavior within latency constraints

## Tradeoffs
- **Pros:** Stable outputs, low latency, easy debugging, robust contract adherence
- **Cons:** Less stylistic variety than generative systems; requires explicit rule expansion for wider trigger nuance
- **State model:** In-memory store is lightweight and fast, but non-durable across process restarts

## Local Validation
Before deployment, local checks validated:
- health and metadata
- context ingestion for category/merchant/trigger
- tick action generation
- reply transitions (intent, auto-reply, hostile)
- message length and response shape compliance
