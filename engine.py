from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Dict, List, Optional

MAX_BODY = 320


def g(d: Any, *keys: str, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def contains(txt: Optional[str], words: List[str]) -> bool:
    txt = (txt or "").lower()
    return any(w in txt for w in words)


def _pct_fmt(x: Optional[float]) -> str:
    if x is None:
        return ""
    pct = round(abs(x) * 100)
    return str(pct)


def _active_offers(merchant: dict) -> List[dict]:
    off = g(merchant, "offers", default=[]) or []
    if not isinstance(off, list):
        return []
    return [o for o in off if isinstance(o, dict) and (o.get("status") in (None, "active", "live"))]


def _pick_offer_title(merchant: dict, category: dict) -> str:
    acts = _active_offers(merchant)
    if acts:
        o = acts[0]
        return (o.get("title") or o.get("name") or o.get("offer") or "your active offer").strip()
    catalog = g(category, "offer_catalog", default=[]) or []
    if catalog and isinstance(catalog[0], dict):
        return (catalog[0].get("title") or "a catalog offer").strip()
    return "a timed offer"


def _digest_item(category: dict, trigger: dict) -> Optional[dict]:
    dig = g(category, "digest", default=[]) or []
    if not isinstance(dig, list):
        return None
    want_id = g(trigger, "payload", "top_item_id")
    if want_id:
        for it in dig:
            if isinstance(it, dict) and it.get("id") == want_id:
                return it
    for it in dig:
        if isinstance(it, dict) and it.get("kind") == "research":
            return it
    for it in dig:
        if isinstance(it, dict):
            return it
    return None


def _doctor_prefix(merchant: dict) -> str:
    owner = g(merchant, "identity", "owner_first_name", default="") or ""
    if not owner:
        return ""
    cat = (g(merchant, "category_slug", default="") or "").lower()
    if "dentist" in cat:
        return f"Dr {owner}"
    return owner


def _finalize_body(text: str) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= MAX_BODY:
        return text
    clipped = text[:MAX_BODY]
    cut = clipped.rfind(" ")
    if cut > 220:
        clipped = clipped[:cut]
    clipped = clipped.rstrip(" ,;:-")
    if not clipped.endswith("?"):
        clipped += "?"
    return clipped


def compose_action(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> Dict[str, Any]:
    """Return fields for one tick action (body, cta, suppression_key, rationale)."""
    cat_slug = (g(category, "slug", default="") or g(merchant, "category_slug", default="generic")).lower()
    kind = (g(trigger, "kind", default="") or "generic").lower()
    name = g(merchant, "identity", "name", default="your business")
    loc = g(merchant, "identity", "locality", default="your area")
    city = g(merchant, "identity", "city", default="")
    salutation = _doctor_prefix(merchant) or g(merchant, "identity", "owner_first_name", default="there")

    offer_t = _pick_offer_title(merchant, category)
    views = g(merchant, "performance", "views")
    calls = g(merchant, "performance", "calls")
    d_views = g(merchant, "performance", "delta_7d", "views_pct")
    signals = g(merchant, "signals", default=[]) or []
    sig_str = ", ".join(signals[:3]) if signals else ""

    digest = _digest_item(category, trigger)
    trig_pay = g(trigger, "payload", default={}) or {}

    body = f"{salutation}, quick growth check for {name} ({loc}) - want a tailored next step?"
    rationale_parts = [f"trigger.kind={kind}", f"merchant={name}", f"locality={loc}"]

    # --- Trigger-aware branches (use substring match on kind) ---
    if contains(kind, ["research_digest", "research"]):
        rationale_parts.append("branch=research_digest")
        if digest:
            src = digest.get("source") or ""
            title = (digest.get("title") or "")[:120]
            trial_n = digest.get("trial_n")
            summ = (digest.get("summary") or "")[:140]
            extra = f" Source: {src}." if src else ""
            if trial_n:
                extra = f" (n={trial_n}).{extra}"
            dip = ""
            if d_views is not None and d_views < 0:
                dip = f" Listing views down {_pct_fmt(d_views)}% vs last week."
            elif contains(str(sig_str), ["perf_dip", "dip"]):
                dip = " Performance dipped vs your usual - worth countering now."
            no_off = contains(str(sig_str), ["no_active_offers"]) or not _active_offers(merchant)
            offer_line = (
                f' Should I line up "{offer_t}" with a short recall note?'
                if no_off
                else f' Push "{offer_t}" with this hook?'
            )
            body = (
                f'{salutation}, JIDA-style recall insight for {loc}: "{title}"{extra}'
                f"{dip} {summ[:100]}{'...' if len(summ) > 100 else ''}{offer_line}"
            )
        else:
            metric = trig_pay.get("metric_or_topic") or "local demand"
            body = (
                f"{salutation}, research alert ({metric}) for {name} near {loc}. "
                f'Lead with "{offer_t}" - shall I draft the 2-line post now?'
            )

    elif contains(kind, ["review_theme", "review_theme_emerged", "review"]):
        rationale_parts.append("branch=review_theme")
        themes = g(merchant, "review_themes", default=[]) or []
        theme = ""
        if themes and isinstance(themes[0], dict):
            theme = themes[0].get("theme") or ""
        focus = f" around '{theme}'" if theme else ""
        body = (
            f"{salutation}, review trend detected{focus} for {name}. "
            f"Should I publish a trust-building post plus '{offer_t}' to convert hesitant leads?"
        )

    elif contains(kind, ["competitor_opened", "competitor", "new_competitor"]):
        rationale_parts.append("branch=competitor_opened")
        body = (
            f"{salutation}, a nearby competitor opened around {loc}. "
            f"Defend share this week by featuring '{offer_t}' with a limited-time slot push?"
        )

    elif contains(kind, ["dip", "slow", "dormant"]):
        rationale_parts.append("branch=dip")
        dip_txt = f"Views trending down {_pct_fmt(d_views)}% in 7d." if d_views is not None and d_views < 0 else "Traffic softer than usual."
        body = f"{salutation}, {dip_txt} at {name}. Restart '{offer_t}' tonight with one clear CTA?"

    elif contains(kind, ["spike", "surge", "milestone"]):
        rationale_parts.append("branch=spike")
        body = (
            f"{salutation}, momentum moment for {name} in {loc} - ride it with '{offer_t}' "
            f"while views/calls are strong. Lock a 48h boost?"
        )

    elif contains(kind, ["festival", "ipl", "match", "event"]):
        rationale_parts.append("branch=festival_or_event")
        if contains(cat_slug, ["restaurant"]):
            body = f"{salutation}, high intent near {loc} tonight. Feature '{offer_t}' for walk-ins?"
        else:
            body = f"{salutation}, seasonal spike window in {city or loc}. Highlight '{offer_t}' now?"

    elif contains(kind, ["recall", "renewal", "refill", "appointment", "trial", "lapsed"]):
        rationale_parts.append("branch=recall_or_crm")
        body = f"{salutation}, follow-ups due for {name}. Use '{offer_t}' in a polite recall blast to past guests?"

    elif contains(cat_slug, ["restaurant"]):
        body = f"{salutation}, {name} in {loc}: spotlight '{offer_t}' to recover visits - one tap to publish?"
    elif contains(cat_slug, ["dentist"]):
        body = f"{salutation}, clinical trust angle for {loc}: pair '{offer_t}' with a short evidence line - OK to draft?"
    elif contains(cat_slug, ["salon"]):
        body = f"{salutation}, bridal/party season angle in {loc} - package '{offer_t}' as a weekend slot filler?"
    elif contains(cat_slug, ["gym"]):
        body = f"{salutation}, win back lapses at {name}: '{offer_t}' + 7-day challenge — should I set it up?"
    elif contains(cat_slug, ["pharmac"]):
        body = f"{salutation}, compliance-friendly nudge for {loc}: remind chronic refills alongside '{offer_t}'?"

    # Numbers grounding when present (never invent views/calls)
    if views is not None and calls is not None and len(body) < 200:
        body += f" (30d views {views}, calls {calls}.)"

    body = _finalize_body(body)

    suppression_key = g(trigger, "suppression_key")
    if not suppression_key:
        raw = f"{g(trigger,'id')}:{cat_slug}:{kind}:{date.today().isoformat()}"
        suppression_key = hashlib.sha256(raw.encode()).hexdigest()[:40]

    return {
        "body": body,
        "cta": "binary_yes_no",
        "sender": "vera",
        "suppression_key": suppression_key,
        "rationale": "; ".join(rationale_parts) + f"; signals=[{sig_str}]",
    }


def compose_message(scope, version, category, merchant, trigger, customer=None):
    """Legacy shape for older callers — maps to compose_action."""
    out = compose_action(category or {}, merchant or {}, trigger or {}, customer)
    return {
        "body": out["body"],
        "cta": out["cta"],
        "sender": out["sender"],
        "suppression_key": out["suppression_key"],
        "rationale": out["rationale"],
    }
