"""JSON output.

The shape is a documented interface, not an accident of the internal dataclasses.
It is versioned so that a consumer can tell when it changes, and every derived
number is accompanied by the counts it came from so a downstream tool can check
the arithmetic rather than trusting it.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import AnalysisReport, TestAnalysis, TriageReport

SCHEMA_VERSION = 1
"""Bump when a field is removed or its meaning changes. Additions do not bump."""


def report_to_dict(report: AnalysisReport) -> dict[str, Any]:
    """Convert an analysis to the documented JSON shape."""
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "runs": report.total_runs,
            "results": report.total_results,
            "tests": len(report.tests),
            "flaky": len(report.flaky),
            "regressions": len(report.regressions),
            "broken": len(report.broken),
            "fixed": len(report.fixed),
            "threshold": report.threshold,
            "window_start": report.window_start,
            "window_end": report.window_end,
            # Callers should check this before trusting scores: false means the
            # primary signal was unavailable.
            "has_commit_data": report.has_commit_data,
            "commit_coverage": round(report.commit_coverage, 4),
        },
        "tests": [_test_to_dict(t) for t in report.tests],
        "clusters": [
            {
                "signature": c.signature,
                "representative_message": c.representative_message,
                "test_ids": list(c.test_ids),
                "test_count": c.test_count,
                "failure_count": c.failure_count,
                "cause": c.cause.cause if c.cause else None,
            }
            for c in report.clusters
        ],
    }


def _test_to_dict(test: TestAnalysis) -> dict[str, Any]:
    return {
        "test_id": test.test_id,
        "name": test.name,
        "suite": test.suite,
        "verdict": str(test.verdict),
        "score": test.score,
        "evidence": {
            "runs": test.runs,
            "passes": test.passes,
            "failures": test.failures,
            "skips": test.skips,
            "retries": test.retries,
            "flips": test.flips,
            "flip_rate": test.flip_rate,
            "divergent_commits": test.divergent_commits,
            "observed_commits": test.observed_commits,
            "divergence_rate": test.divergence_rate,
            "confidence": test.confidence,
            "failure_rate": round(test.failure_rate, 4),
        },
        "first_seen": test.first_seen,
        "last_seen": test.last_seen,
        "last_status": str(test.last_status) if test.last_status else None,
        "consecutive_passes": test.consecutive_passes,
        "signatures": list(test.signatures),
        "representative_message": test.representative_message,
        "cause": (
            {
                "category": str(test.cause.cause),
                "confidence": test.cause.confidence,
                "matched": list(test.cause.matched),
                "remediation": test.cause.remediation,
            }
            if test.cause
            else None
        ),
        "order_dependence": (
            {
                "separation": test.order.separation,
                "mean_position_on_fail": test.order.mean_position_on_fail,
                "mean_position_on_pass": test.order.mean_position_on_pass,
                "likely_polluter": test.order.likely_polluter,
                "polluter_failure_share": test.order.polluter_failure_share,
                "polluter_distance": test.order.polluter_distance,
                "polluter_lift": test.order.polluter_lift,
                "polluter_observations": test.order.polluter_observations,
                "candidates_considered": test.order.candidates_considered,
            }
            if test.order
            else None
        ),
        "environment": [
            {
                "dimension": association.dimension,
                "value": association.value,
                "failures": association.failures,
                "runs": association.runs,
                "failure_rate": round(association.failure_rate, 4),
                "other_failures": association.other_failures,
                "other_runs": association.other_runs,
                "other_rate": round(association.other_rate, 4),
                "lift": association.lift,
                "probability": association.probability,
                "values_considered": association.values_considered,
                "covaries_with": list(association.covaries_with),
            }
            for association in test.environment
        ],
    }


def triage_to_dict(result: TriageReport) -> dict[str, Any]:
    """Convert a triage result to the documented JSON shape."""

    def entry(failure: Any) -> dict[str, Any]:
        return {
            "test_id": failure.test_id,
            "name": failure.name,
            "status": str(failure.status),
            "message": failure.message,
            "score": failure.score,
            "verdict": str(failure.history.verdict) if failure.history else None,
            "failures": failure.history.failures if failure.history else None,
            "runs": failure.history.runs if failure.history else None,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "source": result.source,
            "commit_sha": result.commit_sha,
            "total_tests": result.total_tests,
            "total_failures": result.total_failures,
            "known_flakes": len(result.known_flakes),
            "new_failures": len(result.new_failures),
            "regressions": len(result.regressions),
            "all_known_flaky": result.all_known_flaky,
        },
        "known_flakes": [entry(f) for f in result.known_flakes],
        "new_failures": [entry(f) for f in result.new_failures],
        "regressions": [entry(f) for f in result.regressions],
    }


def render_report(report: AnalysisReport, *, indent: int = 2) -> str:
    return json.dumps(report_to_dict(report), indent=indent, sort_keys=False) + "\n"


def render_triage(result: TriageReport, *, indent: int = 2) -> str:
    return json.dumps(triage_to_dict(result), indent=indent, sort_keys=False) + "\n"
