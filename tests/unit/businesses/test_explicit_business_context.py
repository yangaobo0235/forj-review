"""Concurrent business rules receive their policies explicitly."""

import asyncio
from datetime import date

import pytest

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.field_policies import date_window_problem
from forj_review_service.businesses.page_config.compiler import compile_page
from forj_review_service.businesses.page_config.schema import PageDefinition


def context(name, max_age):
    profile = compile_page(
        PageDefinition.model_validate(
            {
                "page_id": name,
                "business_type": name,
                "title": name,
                "paths": ["/" + name],
                "scopes": [name],
                "materials": [{"document_type": "permit", "label": "许可证"}],
                "fields": [
                    {
                        "key": "permit.issue_date",
                        "label": name,
                        "aliases": [name],
                        "normalizer": "date",
                        "max_age_days": max_age,
                        "evidence": [{"material": "permit", "field": "printed.date"}],
                    }
                ],
            }
        )
    )
    return BusinessContext.from_profile(profile)


@pytest.mark.asyncio
async def test_concurrent_contexts_do_not_share_labels_or_date_policy():
    short, long = context("short", 30), context("long", 90)

    async def evaluate(business):
        results = []
        for _ in range(10):
            await asyncio.sleep(0)
            results.append(
                date_window_problem(
                    "日期",
                    "permit.issue_date",
                    "2026-08-01",
                    today=date(2026, 10, 1),
                    business=business,
                )
            )
        return results

    short_results, long_results = await asyncio.gather(evaluate(short), evaluate(long))
    assert all(result and "30" in result for result in short_results)
    assert long_results == [None] * 10
    assert short.labels["permit.issue_date"] == "short"
    assert long.labels["permit.issue_date"] == "long"
    with pytest.raises(TypeError):
        short.labels["permit.issue_date"] = "mutated"
