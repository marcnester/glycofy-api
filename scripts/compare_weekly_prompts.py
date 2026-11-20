"""Compare unpersisted drafts against one frozen nutrition snapshot.

Uses the configured model/keys in place, never prints credentials, never saves a
meal, and blocks SQL writes in this process. Run outside the web worker process.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import logging
import os
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True, help="Completed seven-day job supplying frozen targets")
    parser.add_argument("--candidate-module", type=Path)
    parser.add_argument("--repeats", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--holdout-days", type=int, default=2, choices=range(0, 4))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    from sqlalchemy import event

    from app.config import settings
    from app.db import SessionLocal, engine
    from app.models import Plan, UserPreference, WeeklyPlanningJob
    from app.routers import llm_recommend as planner
    from app.services import usda_nutrition as usda
    from app.services.meal_quality import QUALITY_POLICY_VERSION, validate_meal
    from app.services.usda_local import load_catalog

    if args.candidate_module:
        spec = importlib.util.spec_from_file_location("glycofy_prompt_candidate", args.candidate_module)
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load candidate module")
        prompt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prompt)
    else:
        from app.services import weekly_prompt as prompt
    os.environ["WEEKLY_PROMPT_VARIANT"] = "control"

    @event.listens_for(engine, "before_cursor_execute")
    def forbid_writes(_conn, _cursor, statement, _parameters, _context, _executemany):
        if not statement.lstrip().upper().startswith(("SELECT", "SHOW")):
            raise RuntimeError("Prompt comparison prevented a database write")

    def plans_digest(db, user_id):
        def row(obj):
            return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

        data = [
            {"plan": row(p), "meals": [{"meal": row(m), "items": [row(i) for i in m.items]} for m in p.meals]}
            for p in db.query(Plan).filter(Plan.user_id == user_id).order_by(Plan.id)
        ]
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

    with SessionLocal() as db:
        job = db.get(WeeklyPlanningJob, args.job_id)
        if job is None or job.status != "completed" or len((job.result or {}).get("days", [])) != 7:
            raise RuntimeError("A completed seven-day source job is required")
        user_id = job.user_id
        before = plans_digest(db, user_id)
        pref = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
        diet = planner._primary_diet_from_preferences(pref)
        exclusions = planner._preference_exclusions(pref)
        tags = planner._diet_tags_from_preferences(pref)
        feedback = planner.feedback_context(db, user_id)
        snack_times = dict(planner._snack_schedule(pref))
        source = copy.deepcopy(job.result)
    days = [
        {
            "date": d["date"],
            "training": d["training"]["training"],
            "diet_tags": [],
            "meals": [
                {"slot": item["slot"], "preferred_time": snack_times.get(item["slot"]), "target_macros": item["target"]}
                for item in d["items"]
            ],
        }
        for d in source["days"]
    ]
    assert len(days) == 7 and all(len(d["meals"]) == 5 for d in days)
    cached_rows = prompt._cached_matches(prompt.cached_food_rows())
    cache = {row["query"]: row for row in cached_rows}
    catalog = load_catalog(settings.USDA_LOCAL_CATALOG_PATH, settings.USDA_LOCAL_CATALOG_SHA256)
    if not catalog or not settings.USDA_FDC_API_KEY:
        raise RuntimeError("This comparison needs the existing USDA configuration")

    def frozen_resolve(queries, **_kwargs):
        resolved = {}
        for query in sorted({q.strip().lower() for q in queries if q.strip()}):
            row = cache.get(usda._query_key(query))
            match = (
                usda.FDCMatch(row["fdc_id"], row["description"], row["dataType"], row["nutrients"])
                if row
                else catalog.lookup(query)
            )
            resolved[query] = match or usda.USDANutritionError("Food unavailable in frozen comparison snapshot")
        return resolved

    # The same food evidence is available to both variants. No live USDA
    # requests, warmed database mappings, validation queue or plan writes.
    planner.resolve_foods = frozen_resolve
    planner.record_ai_operation = lambda *_args, **_kwargs: None
    usda.enqueue_nutrition_validation = lambda *_args, **_kwargs: None
    client = planner._get_openai_client()
    if client is None:
        raise RuntimeError("The existing OpenAI client is not configured")

    class Rejections(logging.Handler):
        def __init__(self):
            super().__init__()
            self.reasons: Counter[str] = Counter()

        def emit(self, record):
            if record.getMessage() == "weekly_meal_rejected":
                self.reasons.update(getattr(record, "reason_codes", []))

    runs = []
    cases = [("athlete_week", days, diet, exclusions, tags)]
    if args.holdout_days:
        cases.append(("vegan_soy_wheat_free", days[: args.holdout_days], "vegan", ["soy", "wheat"], ["vegan"]))
    for case, case_days, case_diet, case_exclusions, case_tags in cases:
        options = prompt.verified_ingredient_options(
            diet=case_diet, exclusions=case_exclusions, diet_tags=case_tags, catalog=catalog, cache_rows=cached_rows
        )
        if not options:
            raise RuntimeError(f"No checked reference context for {case}")
        for repeat in range(args.repeats if case == "athlete_week" else 1):
            for variant in (("control", "candidate") if repeat % 2 == 0 else ("candidate", "control")):
                calls = []

                class Completions:
                    def __init__(self, variant, options, calls):
                        self.variant, self.options, self.calls = variant, options, calls

                    def create(self, **kwargs):
                        kwargs = copy.deepcopy(kwargs)
                        if (
                            self.variant == "candidate"
                            and kwargs.get("response_format", {}).get("json_schema", {}).get("name")
                            == "glycofy_week_plan"
                        ):
                            system = kwargs["messages"][0]["content"]
                            system = system.replace(
                                f"PROMPT_VERSION={planner.PROMPT_VERSION}",
                                f"PROMPT_VERSION={prompt.CANDIDATE_VERSION}",
                                1,
                            )
                            payload = json.loads(kwargs["messages"][1]["content"])
                            system, payload = prompt.evidence_first_prompt(system, payload, self.options)
                            kwargs["messages"] = [
                                {"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                            ]
                        response = client.chat.completions.create(**kwargs)
                        usage, cost = planner._extract_usage_meta(response)
                        self.calls.append({"usage": usage, "cost_usd": cost})
                        return response

                rejection_log = Rejections()
                planner.logger.addHandler(rejection_log)
                started = time.perf_counter()
                try:
                    items, meta = planner._parallel_week_recommendations(
                        SimpleNamespace(chat=SimpleNamespace(completions=Completions(variant, options, calls))),
                        days=copy.deepcopy(case_days),
                        primary_diet=case_diet,
                        diet_tags=case_tags,
                        exclusions=case_exclusions,
                        athlete_feedback=feedback if case == "athlete_week" else {},
                    )
                    _titles, duplicates = planner._deduplicate_week_batch(case_days, items)
                finally:
                    planner.logger.removeHandler(rejection_log)
                unsafe = []
                for day_slots in items.values():
                    for item in day_slots.values():
                        idea = item.ai_idea or {}
                        report = validate_meal(
                            {**idea, "slot": item.slot},
                            diet=case_diet,
                            exclusions=case_exclusions,
                            require_ingredient_nutrition=True,
                            target_miss_severity="warning",
                        )
                        if not report.safe:
                            unsafe.append(report.codes())
                requested = sum(len(d["meals"]) for d in case_days)
                accepted = sum(len(slots) for slots in items.values())
                result = {
                    "case": case,
                    "repeat": repeat + 1,
                    "variant": variant,
                    "prompt_version": prompt.CANDIDATE_VERSION if variant == "candidate" else planner.PROMPT_VERSION,
                    "requested": requested,
                    "accepted_after_variety": accepted,
                    "needs_repair": requested - accepted,
                    "rejected_before_variety": meta.get("rejected"),
                    "duplicates_removed": duplicates,
                    "first_pass_seconds": round(time.perf_counter() - started, 3),
                    "reference_food_count": len(options) if variant == "candidate" else 0,
                    "rejection_reasons": dict(rejection_log.reasons),
                    "unsafe_accepted": unsafe,
                    "cost_usd": round(sum(c["cost_usd"] for c in calls), 6),
                    "model_calls": len(calls),
                }
                runs.append(result)
                args.output.write_text(json.dumps({"complete": False, "runs": runs}, indent=2))
                print("PROMPT_COMPARE_RUN=" + json.dumps(result), flush=True)
                if unsafe or not calls:
                    raise RuntimeError("Comparison safety or model-call check failed")
    with SessionLocal() as db:
        unchanged = plans_digest(db, user_id) == before
    report = {
        "complete": True,
        "model": planner._openai_model(),
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "nutrition_cache_records": len(cached_rows),
        "saved_plans_unchanged": unchanged,
        "sql_write_guard_enabled": True,
        "live_usda_requests": 0,
        "runs": runs,
        "scope": "Unpersisted first pass plus existing variety checks, not end-to-end saved-week timing",
    }
    args.output.write_text(json.dumps(report, indent=2))
    print("PROMPT_COMPARE_COMPLETE=" + json.dumps({"runs": len(runs), "saved_plans_unchanged": unchanged}), flush=True)
    if not unchanged:
        raise RuntimeError("Saved plan fingerprint changed during comparison")


if __name__ == "__main__":
    main()
