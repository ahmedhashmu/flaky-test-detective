import { useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import {
  Box,
  Card,
  CardContent,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";

import { causeLabel, leafName, pct, relative, score2, shortId, shortSha } from "../lib/format";
import { MONO } from "../lib/theme";
import type { TestSummary } from "../lib/types";
import { VerdictChip } from "./VerdictChip";

type Filter = "attention" | "flaky" | "breaks" | "all";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "attention", label: "Needs attention" },
  { value: "flaky", label: "Flaky" },
  { value: "breaks", label: "Breaks" },
  { value: "all", label: "All" },
];

/**
 * The ranked worklist.
 *
 * Every row carries the counts behind its score -- runs, pass/fail, flips, same-commit
 * divergence -- so the table can be checked without opening anything. That is the same
 * rule the CLI follows: no number appears without the evidence that produced it.
 */
export function TestTable({ tests }: { tests: TestSummary[] }) {
  const [filter, setFilter] = useState<Filter>("attention");
  const [query, setQuery] = useState("");

  const rows = useMemo(() => {
    const term = query.trim().toLowerCase();
    return tests.filter((test) => {
      if (term && !test.test_id.toLowerCase().includes(term)) return false;
      switch (filter) {
        case "attention":
          return test.verdict !== "stable";
        case "flaky":
          return test.verdict === "flaky";
        case "breaks":
          return test.verdict === "regression" || test.verdict === "broken";
        case "all":
          return true;
      }
    });
  }, [tests, filter, query]);

  return (
    <Card>
      <CardContent>
        <Stack
          direction={{ xs: "column", md: "row" }}
          justifyContent="space-between"
          alignItems={{ md: "center" }}
          spacing={1.5}
          sx={{ mb: 2 }}
        >
          <Box>
            <Typography variant="h2">Ranked tests</Typography>
            <Typography variant="caption">
              Sorted by score. Counts shown so each verdict can be checked.
            </Typography>
          </Box>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <TextField
              size="small"
              placeholder="Filter by test id"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              sx={{ minWidth: 210 }}
            />
            <ToggleButtonGroup
              size="small"
              exclusive
              value={filter}
              onChange={(_, next: Filter | null) => next && setFilter(next)}
            >
              {FILTERS.map((option) => (
                <ToggleButton key={option.value} value={option.value} sx={{ px: 1.5 }}>
                  {option.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Stack>
        </Stack>

        {rows.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 3, textAlign: "center" }}>
            {filter === "attention"
              ? "Nothing needs attention. No flaky tests, regressions or broken tests."
              : "No tests match that filter."}
          </Typography>
        ) : (
          <Box sx={{ overflowX: "auto" }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell align="right">Score</TableCell>
                  <TableCell>Verdict</TableCell>
                  <TableCell align="right">Runs</TableCell>
                  <TableCell align="right">Pass / Fail</TableCell>
                  <TableCell align="right">Flips</TableCell>
                  <TableCell align="right">
                    <Tooltip title="Commits where the test both passed and failed, over commits where it ran more than once. This is the proof.">
                      <span>Same-commit</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell>Cause</TableCell>
                  <TableCell>Test</TableCell>
                  <TableCell align="right">Last seen</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((test) => (
                  <TableRow key={test.test_id} hover>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      <Tooltip
                        title={`Confidence ${pct(test.confidence)} — damped while fewer than 10 runs are recorded`}
                      >
                        <span style={{ fontWeight: 600 }}>{score2(test.score)}</span>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <VerdictChip verdict={test.verdict} />
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {test.runs}
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {test.passes} / {test.failures}
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {test.flips}
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {test.observed_commits > 0
                        ? `${test.divergent_commits} / ${test.observed_commits}`
                        : "—"}
                    </TableCell>
                    <TableCell>
                      {test.polluter ? (
                        <Tooltip title={`Fails after ${test.polluter}`}>
                          <Typography variant="body2" sx={{ color: "secondary.main" }}>
                            {causeLabel(test.cause)}
                          </Typography>
                        </Tooltip>
                      ) : (
                        <Typography variant="body2" color="text.secondary">
                          {causeLabel(test.cause)}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Link
                        component={RouterLink}
                        to={`/tests/${encodeURIComponent(test.test_id)}`}
                        underline="hover"
                        sx={{ fontFamily: MONO, fontSize: "0.8rem" }}
                      >
                        <Tooltip title={test.test_id}>
                          <span>{shortId(test.test_id, 46)}</span>
                        </Tooltip>
                      </Link>
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="caption">{relative(test.last_seen)}</Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

export { leafName, shortSha };
