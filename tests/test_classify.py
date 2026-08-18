"""Root-cause classification.

Each category is asserted against a message a real runner would actually emit, not
against a string containing the category name. The point of the classifier is to
recognize failures in the wild, and a test written from the rule table would pass
while proving nothing.
"""

from __future__ import annotations

import pytest

from flaky_detective.analysis.classify import classify, remediation_for
from flaky_detective.models import Cause, OrderEvidence


class TestCategories:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            (
                "TimeoutError: operation timed out after 30.0s waiting for lock",
                Cause.TIMEOUT,
            ),
            ("Error: Exceeded timeout of 5000 ms for a test", Cause.TIMEOUT),
            ("context deadline exceeded", Cause.TIMEOUT),
            (
                "ConnectionRefusedError: connection refused to localhost:5432",
                Cause.NETWORK,
            ),
            ("Error: connect ECONNREFUSED 127.0.0.1:8080", Cause.NETWORK),
            ("HTTPError: HTTP 503 Service Unavailable", Cause.NETWORK),
            ("socket.gaierror: getaddrinfo failed", Cause.NETWORK),
            ("OSError: [Errno 24] Too many open files", Cause.RESOURCE),
            ("OSError: [Errno 48] Address already in use", Cause.RESOURCE),
            ("MemoryError: cannot allocate 2 GB", Cause.RESOURCE),
            ("WARNING: DATA RACE detected on shared map", Cause.RACE),
            ("java.util.ConcurrentModificationException", Cause.RACE),
            ("RuntimeError: deadlock detected acquiring mutex", Cause.RACE),
            ("AssertionError: random sample did not include sentinel", Cause.RANDOMNESS),
            ("AssertionError: shuffled order was unexpected", Cause.RANDOMNESS),
            ("AssertionError: token expired before the clock check", Cause.TIME_DEPENDENCE),
            ("ValueError: timezone offset changed across DST boundary", Cause.TIME_DEPENDENCE),
            ("AssertionError: expected 2 but was 3", Cause.ASSERTION),
        ],
    )
    def test_message_maps_to_category(self, message: str, expected: Cause) -> None:
        assert classify([message]).cause is expected

    def test_unknown_when_nothing_matches(self) -> None:
        result = classify(["ImportError: cannot import name 'thing' from 'mod'"])
        assert result.cause is Cause.UNKNOWN
        assert result.confidence == 0.0

    def test_empty_input(self) -> None:
        assert classify([]).cause is Cause.UNKNOWN
        assert classify(["", "  "]).cause is Cause.UNKNOWN


class TestEvidence:
    def test_matched_terms_are_returned(self) -> None:
        """The reader has to be able to see why, and disagree."""
        result = classify(["TimeoutError: timed out after 30s"])
        assert result.matched
        assert any("timed out" in term for term in result.matched)

    def test_remediation_is_present(self) -> None:
        result = classify(["Error: connect ECONNREFUSED 10.0.0.1:80"])
        assert "network boundary" in result.remediation

    def test_more_signals_raise_confidence(self) -> None:
        one = classify(["TimeoutError: timeout"])
        many = classify(["TimeoutError: timed out, deadline exceeded, did not finish"])
        assert many.confidence > one.confidence

    def test_confidence_is_bounded(self) -> None:
        result = classify(
            ["timeout timed out deadline exceeded ETIMEDOUT took too long did not finish"]
        )
        assert result.confidence <= 1.0


class TestPrecedence:
    def test_stronger_rule_wins_over_assertion_fallback(self) -> None:
        """Almost every failure message contains 'assert'; it must not dominate."""
        result = classify(["AssertionError: connection refused to db:5432"])
        assert result.cause is Cause.NETWORK

    def test_timeout_beats_race_when_both_appear(self) -> None:
        result = classify(["TimeoutError: timed out waiting for lock, did not finish"])
        assert result.cause is Cause.TIMEOUT

    def test_order_evidence_overrides_message_rules(self) -> None:
        """A measurement beats a guess."""
        order = OrderEvidence(
            separation=2.0,
            mean_position_on_fail=8.0,
            mean_position_on_pass=2.0,
            likely_polluter="tests/test_a.py::test_seeds_cache",
            polluter_failure_share=1.0,
        )
        result = classify(["TimeoutError: timed out after 30s"], order)
        assert result.cause is Cause.ORDER_DEPENDENCE
        assert result.confidence == 0.9

    def test_order_evidence_names_the_polluter_in_its_evidence(self) -> None:
        order = OrderEvidence(
            separation=1.5,
            mean_position_on_fail=9.0,
            mean_position_on_pass=3.0,
            likely_polluter="tests/test_a.py::test_polluter",
            polluter_failure_share=0.9,
        )
        result = classify(["AssertionError: boom"], order)
        assert any("test_polluter" in term for term in result.matched)


class TestRawVersusNormalized:
    def test_http_status_survives_because_raw_text_is_used(self) -> None:
        """Normalization turns `HTTP 503` into `HTTP <NUM>`, losing the signal.

        This is why the classifier reads the raw message. If it ever switches to the
        normalized one, this test fails.
        """
        assert classify(["Received HTTP 503 from upstream"]).cause is Cause.NETWORK

    def test_normalized_text_would_lose_it(self) -> None:
        from flaky_detective.normalize import normalize_message

        assert "503" not in normalize_message("Received HTTP 503 from upstream")


class TestRemediationLookup:
    @pytest.mark.parametrize("cause", list(Cause))
    def test_every_cause_has_advice(self, cause: Cause) -> None:
        assert remediation_for(cause)
