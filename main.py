from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from engine import compose_action, MAX_BODY

APP_STARTED = time.time()

app = FastAPI()

# --- Stateful stores (in-memory; survive until process restart) ---
_categories: Dict[str, dict] = {}
_merchants: Dict[str, dict] = {}
_customers: Dict[str, dict] = {}
_triggers: Dict[str, dict] = {}

# Idempotency: key "scope:context_id" -> stored version int
_versions: Dict[str, int] = {}

# Optional: avoid spamming same suppression across ticks (adaptive behavior)
_sent_suppression: set = set()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _version_key(scope: str, context_id: str) -> str:
    return f"{scope}:{context_id}"


class ContextRequest(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


class TickRequest(BaseModel):
    now: Optional[str] = None
    available_triggers: List[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str = ""
    received_at: Optional[str] = None
    turn_number: int = 1


@app.get("/")
def root():
    return {"status": "ok", "service": "vera-message-engine", "message": "Dhwani bot is running"}


@app.get("/v1/healthz")
def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - APP_STARTED),
        "contexts_loaded": {
            "category": len(_categories),
            "merchant": len(_merchants),
            "customer": len(_customers),
            "trigger": len(_triggers),
        },
    }


@app.get("/v1/metadata")
def metadata():
    return {
        "team_name": "Dhwani Bot",
        "team_members": ["Dhwani"],
        "model": "deterministic-compose-v1",
        "approach": "Rule-based composer with JSON-grounded slots; trigger-first routing",
        "contact_email": "dhwanisaliya@gmail.com",
        "version": "1.0.0",
        "url": "https://vera-dhwani-bot.onrender.com",
        "submitted_at": _utc_iso(),
    }


@app.post("/v1/context")
def context(req: ContextRequest, response: Response):
    key = _version_key(req.scope, req.context_id)
    prev = _versions.get(key, 0)

    if req.version < prev:
        response.status_code = 409
        return {"accepted": False, "reason": "stale_version", "current_version": prev}

    if req.version == prev and prev > 0:
        # Duplicate delivery of same version: acknowledge without rewriting
        return {
            "accepted": True,
            "ack_id": f"ack_dup_{req.context_id}_v{req.version}",
            "stored_at": _utc_iso(),
        }

    if req.scope == "category":
        slug = req.payload.get("slug") or req.context_id
        _categories[slug] = req.payload
    elif req.scope == "merchant":
        mid = req.payload.get("merchant_id") or req.context_id
        _merchants[mid] = req.payload
    elif req.scope == "customer":
        cid = req.payload.get("customer_id") or req.context_id
        _customers[cid] = req.payload
    elif req.scope == "trigger":
        tid = req.payload.get("id") or req.context_id
        _triggers[tid] = req.payload
    else:
        response.status_code = 400
        return {"accepted": False, "reason": "invalid_scope", "details": req.scope}

    _versions[key] = req.version
    return {
        "accepted": True,
        "ack_id": f"ack_{req.context_id}_v{req.version}",
        "stored_at": _utc_iso(),
    }


@app.post("/v1/tick")
def tick(body: TickRequest):
    actions: List[Dict[str, Any]] = []
    max_actions = 20

    for tid in body.available_triggers[:max_actions]:
        trig = _triggers.get(tid)
        if not trig:
            continue
        mid = trig.get("merchant_id")
        if not mid:
            continue
        merchant = _merchants.get(mid, {})
        cat_slug = merchant.get("category_slug") or ""
        category = _categories.get(cat_slug, {})
        cust_id = trig.get("customer_id")
        customer = _customers.get(cust_id) if cust_id else None

        composed = compose_action(category, merchant, trig, customer)
        sk = composed["suppression_key"]
        if sk in _sent_suppression:
            continue
        _sent_suppression.add(sk)

        conv_id = f"conv_{tid}_{mid}"

        msg_body = composed["body"]
        if len(msg_body) > MAX_BODY:
            msg_body = msg_body[: MAX_BODY - 1] + "…"

        actions.append(
            {
                "conversation_id": conv_id,
                "merchant_id": mid,
                "customer_id": cust_id,
                "send_as": "vera",
                "trigger_id": tid,
                "template_name": "vera_deterministic_v1",
                "template_params": [],
                "body": msg_body,
                "cta": composed["cta"],
                "suppression_key": sk,
                "rationale": composed["rationale"],
            }
        )

    return {"actions": actions}


@app.post("/v1/reply")
def reply(req: ReplyRequest):
    text = (req.message or "").strip()
    low = text.lower()

    # Auto-reply / canned greeting (replay-style)
    if any(
        p in low
        for p in (
            "thank you for contacting",
            "our team will respond",
            "we will get back to you",
            "hours of operation",
        )
    ):
        return {
            "action": "wait",
            "wait_seconds": 14400,
            "rationale": "Likely auto-reply; back off and retry later via tick",
        }

    # Hostile / opt-out
    if any(
        p in low
        for p in (
            "stop messaging",
            "don't message",
            "do not message",
            "this is spam",
            "useless spam",
            "not interested",
            "leave me alone",
        )
    ):
        return {"action": "end", "rationale": "Merchant opted out or hostile — close thread"}

    # Commitment / intent transition — move to concrete next step (brief §reply)
    if any(
        p in low
        for p in (
            "ok let's",
            "ok lets",
            "let's do",
            "lets do",
            "what's next",
            "whats next",
            "do it",
            "go ahead",
        )
    ):
        return {
            "action": "send",
            "body": (
                "Here is the next step: I will queue your offer post for peak evening hours "
                "and send you one preview line you can edit. Confirm timing as 6–9pm local?"
            )[:MAX_BODY],
            "cta": "binary_yes_no",
            "rationale": "Merchant committed — switch from discovery to execution",
        }

    # Positive short affirmations
    if any(w in low for w in ("yes", "yeah", "yep", "ok", "okay", "send", "sure", "please")):
        return {
            "action": "send",
            "body": "Queued. You will see the draft in chat shortly — reply PAUSE anytime to hold.",
            "cta": "open_ended",
            "rationale": "Acknowledged assent; confirm delivery",
        }

    if any(w in low for w in ("later", "busy", "tomorrow", "remind me")):
        return {
            "action": "wait",
            "wait_seconds": 3600,
            "rationale": "Merchant asked for time",
        }

    if any(w in low for w in ("cost", "price", "expensive", "budget")):
        return {
            "action": "send",
            "body": "We can start with the smallest boost (no add-ons). Want that lean version?",
            "cta": "binary_yes_no",
            "rationale": "Objection handling — lower-friction option",
        }

    return {
        "action": "wait",
        "wait_seconds": 600,
        "rationale": "Clarify merchant intent before pushing again",
    }
