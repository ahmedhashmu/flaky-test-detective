"""Message normalization.

Every substitution gets a case, and so does the ordering between them, because the
order is load-bearing: several of these rules would eat each other's input if
rearranged, and the bugs that causes are silent. A wrong signature does not raise;
it just splits one bug into forty clusters.
"""

from __future__ import annotations

import pytest

from flaky_detective.normalize import (
    SIGNATURE_MAX_LENGTH,
    normalize_message,
    normalize_test_id,
    salient_line,
    signature_of,
)


class TestSubstitutions:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("run 550e8400-e29b-41d4-a716-446655440000 failed", "run <UUID> failed"),
            ("at 2026-08-18T13:40:32.954582+05:00", "at <TIMESTAMP>"),
            ("started 2026-08-18 13:40:32", "started <TIMESTAMP>"),
            ("deadlock at 11:02:33", "deadlock at <TIME>"),
            ("GET https://api.example.com/v1/items?id=9 failed", "GET <URL> failed"),
            ("object at 0x7f8a3c00d1e0", "object at <ADDR>"),
            ("connect to 10.0.0.42 refused", "connect to <IP> refused"),
            ("open /private/var/folders/ab/T/pytest-of-me/x", "open <TMP>"),
            ("read /tmp/build/artifact.json", "read <TMP>"),
            ("import from /Users/me/proj/src/mod.py", "import from <PATH>"),
            ("failed at line 214", "failed at line <N>"),
            ("took 1.234s", "took <DURATION>"),
            ("elapsed 45ms", "elapsed <DURATION>"),
            ("processed 12345 rows", "processed <NUM> rows"),
            ("collapse    the   spaces", "collapse the spaces"),
        ],
    )
    def test_each_rule_fires(self, raw: str, expected: str) -> None:
        assert normalize_message(raw) == expected

    def test_short_integers_survive(self) -> None:
        """`expected 2, got 3` must stay readable; only long numbers are noise."""
        assert normalize_message("expected 2, got 3") == "expected 2, got 3"

    def test_empty_and_none(self) -> None:
        assert normalize_message(None) == ""
        assert normalize_message("") == ""
        assert normalize_message("   ") == ""

    def test_truncates_long_messages(self) -> None:
        result = normalize_message("x" * (SIGNATURE_MAX_LENGTH + 500))
        assert len(result) == SIGNATURE_MAX_LENGTH + 3
        assert result.endswith("...")


class TestSubstitutionOrdering:
    """These assertions exist because each of them was once wrong."""

    def test_ip_survives_the_integer_rule(self) -> None:
        """`127.0.0.1` must not become `<NUM>.0.0.1`."""
        assert normalize_message("ECONNREFUSED 127.0.0.1:8080") == "ECONNREFUSED <IP>:<PORT>"

    def test_source_location_is_not_a_port(self) -> None:
        """`store_test.go:41` is a line number, not a hostname with a port.

        Checked mid-message, because a leading location is now stripped entirely (see
        below) and would hide whether this rule still works.
        """
        assert normalize_message("bad at store_test.go:41 here") == "bad at store_test.go:<N> here"

    def test_a_leading_source_location_is_dropped(self) -> None:
        """Go prefixes every failure with one, and it is the assertion's address,
        not the cause. Keeping it stopped two Go tests in different files from ever
        clustering on a shared cause."""
        assert normalize_message("store_test.go:41: connection refused") == "connection refused"
        assert normalize_message("basket_test.go:7: connection refused") == "connection refused"

    def test_a_mid_message_location_is_kept(self) -> None:
        """There it usually distinguishes genuinely different failures."""
        result = normalize_message("assertion failed, see helper.py:12 for the fixture")
        assert "helper.py:<N>" in result

    def test_real_host_port_is_still_a_port(self) -> None:
        """The fix above must not stop `example.com:8080` being read as a port."""
        assert normalize_message("dial example.com:8080") == "dial example.com:<PORT>"

    def test_uuid_beats_the_address_rule(self) -> None:
        result = normalize_message("id 550e8400-e29b-41d4-a716-446655440000")
        assert result == "id <UUID>"

    def test_duration_beats_the_integer_rule(self) -> None:
        assert normalize_message("waited 5000 ms") == "waited <DURATION>"

    def test_temp_path_beats_the_general_path_rule(self) -> None:
        assert normalize_message("wrote /tmp/a/b/c.json") == "wrote <TMP>"


class TestCollectionCollapsing:
    """Lists of values vary per run while describing one bug."""

    def test_number_lists(self) -> None:
        result = normalize_message("sample [1, 5, 9, 12, 17] missing 0")
        assert result == "sample [<NUMS>] missing 0"

    def test_string_lists_single_quoted(self) -> None:
        result = normalize_message("order was ['beta', 'alpha', 'gamma']")
        assert result == "order was [<LIST>]"

    def test_string_lists_double_quoted(self) -> None:
        assert normalize_message('order was ["b", "a"]') == "order was [<LIST>]"

    def test_two_runs_of_one_bug_share_a_signature(self) -> None:
        first = normalize_message("shuffled order was ['beta', 'alpha'] -- depends on order")
        second = normalize_message("shuffled order was ['alpha', 'beta'] -- depends on order")
        assert first == second


class TestSignatureOf:
    def test_prefers_the_message(self) -> None:
        assert signature_of("AssertionError: nope", "some traceback") == "AssertionError: nope"

    def test_falls_back_to_detail(self) -> None:
        detail = "Traceback (most recent call last):\n  File x\nValueError: bad input"
        assert signature_of(None, detail) == "ValueError: bad input"

    def test_uses_only_the_first_line(self) -> None:
        assert signature_of("first line\nsecond line") == "first line"

    def test_empty(self) -> None:
        assert signature_of(None, None) == ""
        assert signature_of("", "") == ""


class TestSalientLine:
    def test_finds_bare_node_error(self) -> None:
        stack = "Error: expect(received).toEqual(expected)\n    at Object.<anonymous> (x.js:7:1)"
        assert salient_line(stack).startswith("Error: expect(received)")

    def test_finds_jvm_exception(self) -> None:
        detail = "java.lang.AssertionError: expected 2\n\tat com.acme.T.m(T.java:44)"
        assert salient_line(detail).startswith("java.lang.AssertionError")

    def test_strips_pytest_gutter_marker(self) -> None:
        detail = "    assert 1 == 2\nE       AssertionError: assert 1 == 2"
        assert salient_line(detail) == "AssertionError: assert 1 == 2"

    def test_falls_back_to_last_line_for_go(self) -> None:
        detail = "WARNING: DATA RACE\nWrite at 0x00c0\n    store_test.go:103: race detected"
        assert salient_line(detail) == "store_test.go:103: race detected"

    def test_empty(self) -> None:
        assert salient_line(None) == ""
        assert salient_line("") == ""


class TestNormalizeTestId:
    def test_leaves_ordinary_ids_alone(self) -> None:
        assert normalize_test_id("tests/test_a.py::test_b") == "tests/test_a.py::test_b"

    def test_preserves_digits_outside_brackets(self) -> None:
        """A test named `test_http2` must not become `test_http<X>`."""
        assert normalize_test_id("tests/test_http2.py::test_v1") == "tests/test_http2.py::test_v1"

    def test_keeps_stable_parameters(self) -> None:
        assert normalize_test_id("t.py::test_x[3]") == "t.py::test_x[3]"

    def test_scrubs_noise_inside_parameters(self) -> None:
        """A parameter holding a fresh temp path would fragment history every run."""
        first = normalize_test_id("t.py::test_x[0x7f2ab4c01234]")
        second = normalize_test_id("t.py::test_x[0x7f2ab4c09999]")
        assert first == second

    def test_scrubs_uuid_parameters(self) -> None:
        a = normalize_test_id("t.py::test_x[550e8400-e29b-41d4-a716-446655440000]")
        b = normalize_test_id("t.py::test_x[660e8400-e29b-41d4-a716-446655440111]")
        assert a == b

    def test_empty(self) -> None:
        assert normalize_test_id("") == ""
