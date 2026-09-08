"""AI analysis generation for repositories via TokenRouter."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DISCLAIMER = "Generated analysis — not verified GitHub facts."


def _ai_client() -> AsyncOpenAI:
    """OpenAI-compatible client pointed at TokenRouter (or any compatible base URL)."""
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base.rstrip("/"),
    )


def _extract_json(content: str) -> dict[str, Any]:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            return json.loads(match.group(0))
        raise


async def generate_repository_analysis(repo: dict[str, Any]) -> dict[str, Any]:
    if not settings.ai_enabled or not settings.openai_api_key:
        return _fallback_analysis(repo)

    client = _ai_client()
    prompt = f"""
Analyze this open-source GitHub repository for an early-signal discovery product.
Return strict JSON with keys:
what_it_does, why_trending, target_users, technology_stack, maturity_summary,
potential_use_cases, similar_repositories, reasons_to_watch, risks_limitations.

Repository:
full_name: {repo.get('full_name')}
description: {repo.get('description')}
language: {repo.get('language')}
topics: {', '.join(repo.get('topics') or [])}
stars: {repo.get('stars')}
forks: {repo.get('forks')}
license: {repo.get('license')}
created_at: {repo.get('created_at')}
momentum_score: {repo.get('momentum_score')}
labels: {', '.join(repo.get('labels') or [])}
"""
    messages = [
        {
            "role": "system",
            "content": (
                "You analyze open-source projects. Be concise and practical. "
                "Never claim unverified security or commercial viability. "
                "Respond with JSON only."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    last_error: Exception | None = None
    for model in settings.ai_model_list:
        try:
            summary = await _request_analysis(client, model, messages)
            summary["disclaimer"] = DISCLAIMER
            summary["model_used"] = model
            return summary
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "ai_model_failed",
                error=str(exc),
                model=model,
                repo=repo.get("full_name"),
            )

    logger.error(
        "ai_analysis_failed",
        error=str(last_error),
        repo=repo.get("full_name"),
        base_url=settings.openai_api_base,
        models=settings.ai_model_list,
    )
    return _fallback_analysis(repo)


async def _request_analysis(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    # Prefer JSON mode; some free TokenRouter models reject response_format.
    try:
        response = await client.chat.completions.create(
            model=model,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=messages,
        )
    except Exception:
        response = await client.chat.completions.create(
            model=model,
            temperature=0.3,
            messages=messages,
        )

    content = response.choices[0].message.content or "{}"
    return _extract_json(content)


def _fallback_analysis(repo: dict[str, Any]) -> dict[str, Any]:
    desc = repo.get("description") or "No description provided on GitHub."
    language = repo.get("language") or "Unknown"
    topics = repo.get("topics") or []
    return {
        "what_it_does": desc,
        "why_trending": (
            f"Showing momentum signals with {repo.get('stars_gained_7d', 0)} stars "
            f"gained in 7 days and score {repo.get('momentum_score', 'n/a')}."
        ),
        "target_users": "Developers and teams exploring emerging open-source tools.",
        "technology_stack": [language, *topics[:5]],
        "maturity_summary": (
            f"Repository has {repo.get('stars', 0)} stars and "
            f"{repo.get('open_issues', 0)} open issues."
        ),
        "potential_use_cases": [
            "Evaluate as an early technology signal",
            "Prototype integrations",
            "Track competitor/adjacent tooling",
        ],
        "similar_repositories": [],
        "reasons_to_watch": repo.get("labels") or ["Early momentum"],
        "risks_limitations": [
            "AI/heuristic summary only",
            "License and maintenance quality not verified",
            "Star velocity can reverse quickly",
        ],
        "disclaimer": DISCLAIMER,
        "model_used": "fallback",
    }
