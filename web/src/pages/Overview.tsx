import { Link as RouterLink } from "react-router-dom";

import {
  Alert,
  AlertTitle,
  Box,
  Card,
  CardContent,
  Chip,
  Grid,
  Link,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";

import { TestTable } from "../components/TestTable";
import { TrustScoreCard } from "../components/TrustScoreCard";
import { causeLabel, oneLine, pct, when } from "../lib/format";
import { MONO } from "../lib/theme";
import type { Overview as OverviewData } from "../lib/types";

/**
 * Answers one question above the fold: can I trust my CI right now?
 *
 * Deliberately not a generic analytics page. Anything that does not help decide
 * "investigate or re-run" belongs further down or on the detail page.
 */
export function Overview({ data }: { data: OverviewData }) {
  const { trust, summary, tests, clusters, quarantine, caveats } = data;
  const attention = summary.regressions + summary.broken;

  return (
    <Stack spacing={2.5}>
      {caveats.map((caveat) => (
        <Alert key={caveat.title} severity={caveat.severity} variant="outlined">
          <AlertTitle sx={{ mb: 0.25 }}>{caveat.title}</AlertTitle>
          <Typography variant="body2">{caveat.detail}</Typography>
        </Alert>
      ))}

      <Grid container spacing={2.5}>
        <Grid item xs={12} md={5} lg={4}>
          <TrustScoreCard trust={trust} />
        </Grid>

        <Grid item xs={12} md={7} lg={8}>
          <Stack spacing={2.5} sx={{ height: "100%" }}>
            <Grid container spacing={2}>
              <Grid item xs={6} sm={3}>
                <StatCard
                  label="Needs attention"
                  value={attention}
                  tone={attention > 0 ? "error" : "success"}
                  hint="Regressions and never-passing tests. These need a human, not a re-run."
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <StatCard
                  label="Active flakes"
                  value={summary.flaky}
                  tone={summary.flaky > 0 ? "warning" : "success"}
                  hint="Different outcomes for the same code."
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <StatCard
                  label="Runs recorded"
                  value={summary.runs}
                  tone={summary.runs < 10 ? "warning" : "neutral"}
                  hint={
                    summary.runs < 10
                      ? "Measured accuracy drops below 10 runs. Try `flaky hunt -n 20`."
                      : "More runs means more confident scores."
                  }
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <StatCard
                  label="Tests tracked"
                  value={summary.tests}
                  tone="neutral"
                  hint={`${summary.results} recorded results in total`}
                />
              </Grid>
            </Grid>

            <Card sx={{ flexGrow: 1 }}>
              <CardContent>
                <Typography variant="h2" sx={{ mb: 0.5 }}>
                  Window
                </Typography>
                <Typography variant="caption" sx={{ display: "block", mb: 2 }}>
                  {summary.window_start && summary.window_end
                    ? `${when(summary.window_start)} to ${when(summary.window_end)}`
                    : "No runs recorded yet"}
                </Typography>

                <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1}>
                  {Object.entries(summary.runners).map(([runner, count]) => (
                    <Chip
                      key={runner}
                      size="small"
                      variant="outlined"
                      label={`${runner} · ${count} run${count === 1 ? "" : "s"}`}
                    />
                  ))}
                  <Tooltip title="Same-commit divergence is the strongest signal. Verdicts are measurably weaker without it.">
                    <Chip
                      size="small"
                      variant="outlined"
                      color={summary.has_commit_data ? "success" : "warning"}
                      label={`commit evidence ${pct(summary.commit_coverage)}`}
                    />
                  </Tooltip>
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`flake threshold ${summary.threshold.toFixed(2)}`}
                  />
                </Stack>

                {quarantine.available && (
                  <Box sx={{ mt: 2.5, pt: 2, borderTop: "1px solid", borderColor: "divider" }}>
                    <Typography variant="body2" sx={{ fontWeight: 500, mb: 0.5 }}>
                      Quarantine
                    </Typography>
                    <Typography variant="caption">
                      {quarantine.active.length} active,{" "}
                      <Box
                        component="span"
                        sx={{ color: quarantine.expired.length ? "warning.main" : "inherit" }}
                      >
                        {quarantine.expired.length} expired
                      </Box>
                      {quarantine.recommended.length > 0 &&
                        ` · ${quarantine.recommended.length} recommended`}
                    </Typography>
                  </Box>
                )}
              </CardContent>
            </Card>
          </Stack>
        </Grid>
      </Grid>

      <TestTable tests={tests} />

      {clusters.length > 0 && (
        <Card>
          <CardContent>
            <Typography variant="h2">Shared failure signatures</Typography>
            <Typography variant="caption" sx={{ display: "block", mb: 2 }}>
              One cause, several tests. Usually the cheapest thing to fix.
            </Typography>
            <Stack spacing={1.5}>
              {clusters.map((cluster) => (
                <Box
                  key={cluster.signature}
                  sx={{
                    p: 1.5,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                  }}
                >
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}>
                    <Chip
                      size="small"
                      label={`${cluster.test_count} tests`}
                      sx={{ bgcolor: "#1e2430" }}
                    />
                    <Chip
                      size="small"
                      variant="outlined"
                      label={`${cluster.failure_count} failures`}
                    />
                    {cluster.cause && (
                      <Typography variant="caption" sx={{ color: "secondary.main" }}>
                        {causeLabel(cluster.cause)}
                      </Typography>
                    )}
                  </Stack>
                  <Typography
                    variant="body2"
                    sx={{ fontFamily: MONO, fontSize: "0.78rem", color: "text.secondary" }}
                  >
                    {oneLine(cluster.signature, 150)}
                  </Typography>
                  <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1} sx={{ mt: 1 }}>
                    {cluster.test_ids.slice(0, 6).map((testId) => (
                      <Link
                        key={testId}
                        component={RouterLink}
                        to={`/tests/${encodeURIComponent(testId)}`}
                        underline="hover"
                        sx={{ fontFamily: MONO, fontSize: "0.72rem" }}
                      >
                        {testId.split("::").pop()}
                      </Link>
                    ))}
                    {cluster.test_ids.length > 6 && (
                      <Typography variant="caption">
                        +{cluster.test_ids.length - 6} more
                      </Typography>
                    )}
                  </Stack>
                </Box>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}
    </Stack>
  );
}

function StatCard({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: number;
  tone: "error" | "warning" | "success" | "neutral";
  hint: string;
}) {
  const color =
    tone === "error"
      ? "error.main"
      : tone === "warning"
        ? "warning.main"
        : tone === "success"
          ? "success.main"
          : "text.primary";

  return (
    <Tooltip title={hint}>
      <Card sx={{ height: "100%", cursor: "help" }}>
        <CardContent sx={{ py: 1.75 }}>
          <Typography
            sx={{ fontSize: "1.75rem", fontWeight: 700, lineHeight: 1.1, color }}
          >
            {value}
          </Typography>
          <Typography variant="caption">{label}</Typography>
        </CardContent>
      </Card>
    </Tooltip>
  );
}
