import { useEffect, useState } from "react";
import { Link as RouterLink, useParams } from "react-router-dom";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import PsychologyIcon from "@mui/icons-material/Psychology";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  Grid,
  IconButton,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";

import { RunTimeline } from "../components/RunTimeline";
import { VerdictChip } from "../components/VerdictChip";
import { ErrorState, Loading } from "../components/States";
import { fetchTestDetail } from "../lib/api";
import { causeLabel, leafName, oneLine, pct, score2, shortSha, when } from "../lib/format";
import { MONO } from "../lib/theme";
import type { TestDetail as TestDetailData } from "../lib/types";

/**
 * One test, four questions: what is the evidence, what happened over time, why is it
 * failing, and what should I do.
 *
 * The evidence section separates proof from inference visually. That separation is the
 * point of the page: a measured fact and a pattern match must not look alike, or the
 * weaker one borrows the authority of the stronger.
 */
export function TestDetail() {
  const { testId = "" } = useParams();
  const [data, setData] = useState<TestDetailData | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    fetchTestDetail(testId)
      .then((payload) => active && setData(payload))
      .catch((cause) => active && setError(cause));
    return () => {
      active = false;
    };
  }, [testId]);

  if (error) return <ErrorState error={error} />;
  if (!data) return <Loading label="Loading test history" />;

  const { test, evidence, timeline, diagnosis, blame, signatures, neighbours, actions } = data;

  return (
    <Stack spacing={2.5}>
      <Box>
        <Button
          component={RouterLink}
          to="/"
          startIcon={<ArrowBackIcon />}
          size="small"
          sx={{ mb: 1.5, ml: -0.5 }}
        >
          All tests
        </Button>

        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <Typography variant="h1">{leafName(test.test_id)}</Typography>
          <VerdictChip verdict={test.verdict} size="medium" />
          {data.quarantined && (
            <Chip
              size="small"
              color={data.quarantined.expired ? "warning" : "default"}
              variant="outlined"
              label={
                data.quarantined.expired
                  ? "quarantine expired"
                  : `quarantined, ${data.quarantined.days_remaining}d left`
              }
            />
          )}
        </Stack>
        <Typography sx={{ fontFamily: MONO, fontSize: "0.8rem", color: "text.secondary", mt: 0.5 }}>
          {test.test_id}
        </Typography>
      </Box>

      <Grid container spacing={2.5}>
        {/* ---------- Evidence ---------- */}
        <Grid item xs={12} lg={7}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                <FactCheckIcon sx={{ fontSize: 19, color: "success.main" }} />
                <Typography variant="h2">Evidence</Typography>
              </Stack>

              {evidence.proven.length > 0 ? (
                <Stack spacing={1.5}>
                  <Typography
                    variant="overline"
                    sx={{ color: "success.main", letterSpacing: "0.08em" }}
                  >
                    Proven by the detector
                  </Typography>
                  {evidence.proven.map((item) => (
                    <Box
                      key={item.label}
                      sx={{
                        pl: 1.5,
                        borderLeft: "3px solid",
                        borderColor: "success.main",
                      }}
                    >
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {item.label}
                      </Typography>
                      <Typography variant="caption" sx={{ lineHeight: 1.5 }}>
                        {item.detail}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              ) : (
                <Alert severity="info" variant="outlined" sx={{ mb: 2 }}>
                  No direct proof available. Nothing here has been observed passing and
                  failing at one commit, so the verdict rests on the weaker signals below.
                </Alert>
              )}

              {evidence.inferred.length > 0 && (
                <>
                  <Divider sx={{ my: 2 }} />
                  <Stack spacing={1.5}>
                    <Typography
                      variant="overline"
                      sx={{ color: "text.secondary", letterSpacing: "0.08em" }}
                    >
                      Inferred, weaker
                    </Typography>
                    {evidence.inferred.map((item) => (
                      <Box
                        key={item.label}
                        sx={{ pl: 1.5, borderLeft: "3px solid", borderColor: "divider" }}
                      >
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                          {item.label}
                        </Typography>
                        <Typography variant="caption" sx={{ lineHeight: 1.5 }}>
                          {item.detail}
                        </Typography>
                      </Box>
                    ))}
                  </Stack>
                </>
              )}

              <Divider sx={{ my: 2 }} />
              <Stack direction="row" flexWrap="wrap" useFlexGap spacing={2.5}>
                <Metric label="Score" value={score2(test.score)} />
                <Metric label="Divergence rate" value={pct(evidence.score_breakdown.divergence_rate)} />
                <Metric label="Flip rate" value={pct(evidence.score_breakdown.flip_rate)} />
                <Metric label="Confidence" value={pct(evidence.score_breakdown.confidence)} />
                <Metric label="Runs" value={`${test.passes} pass / ${test.failures} fail`} />
              </Stack>
            </CardContent>
          </Card>
        </Grid>

        {/* ---------- Why ---------- */}
        <Grid item xs={12} lg={5}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                <PsychologyIcon sx={{ fontSize: 19, color: "secondary.main" }} />
                <Typography variant="h2">Why</Typography>
              </Stack>

              {diagnosis ? (
                <Stack spacing={1.5}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Chip
                      size="small"
                      label={causeLabel(diagnosis.cause)}
                      sx={{ bgcolor: "#fbbf2422", color: "secondary.main" }}
                    />
                    <Typography variant="caption">
                      {diagnosis.is_heuristic
                        ? `heuristic, confidence ${pct(diagnosis.confidence)}`
                        : "measured from run positions"}
                    </Typography>
                  </Stack>

                  {diagnosis.order?.likely_polluter && (
                    <Box
                      sx={{
                        p: 1.5,
                        borderRadius: 1,
                        bgcolor: "#fbbf2410",
                        border: "1px solid #fbbf2433",
                      }}
                    >
                      <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
                        Fails after another test
                      </Typography>
                      <Link
                        component={RouterLink}
                        to={`/tests/${encodeURIComponent(diagnosis.order.likely_polluter)}`}
                        sx={{ fontFamily: MONO, fontSize: "0.78rem" }}
                      >
                        {leafName(diagnosis.order.likely_polluter)}
                      </Link>
                      <Typography variant="caption" sx={{ display: "block", mt: 0.5 }}>
                        Precedes {pct(diagnosis.order.polluter_failure_share)} of this test's
                        failures, more often than its own base failure rate explains. Retrying
                        will not fix this — the state is already polluted.
                      </Typography>
                    </Box>
                  )}

                  {diagnosis.matched.length > 0 && !diagnosis.order?.likely_polluter && (
                    <Box>
                      <Typography variant="caption">Matched in failure messages:</Typography>
                      <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.75} sx={{ mt: 0.5 }}>
                        {diagnosis.matched.map((term) => (
                          <Chip
                            key={term}
                            size="small"
                            variant="outlined"
                            label={term}
                            sx={{ fontFamily: MONO, fontSize: "0.68rem" }}
                          />
                        ))}
                      </Stack>
                    </Box>
                  )}

                  <Box sx={{ pt: 1 }}>
                    <Typography variant="overline" sx={{ color: "text.secondary" }}>
                      Suggested fix
                    </Typography>
                    <Typography variant="body2" sx={{ lineHeight: 1.55 }}>
                      {diagnosis.remediation}
                    </Typography>
                  </Box>

                  {signatures.length > 1 && (
                    <Alert severity="info" variant="outlined" sx={{ mt: 1 }}>
                      {signatures.length} distinct failure signatures — this is probably more
                      than one bug.
                    </Alert>
                  )}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  No failures recorded, so there is nothing to diagnose.
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* ---------- Timeline ---------- */}
      <Card>
        <CardContent>
          <Typography variant="h2">Timeline</Typography>
          <Typography variant="caption" sx={{ display: "block", mb: 2 }}>
            Oldest first. Runs grouped by commit; a group holding both colours is proof.
          </Typography>
          <RunTimeline timeline={timeline} />
        </CardContent>
      </Card>

      {/* ---------- Blame ---------- */}
      <Card>
        <CardContent>
          <Typography variant="h2">When it started</Typography>
          <Typography variant="caption" sx={{ display: "block", mb: 2 }}>
            {blame.explanation}
          </Typography>

          {blame.actionable && (
            <Stack direction="row" spacing={3} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
              <Box>
                <Typography variant="caption">First diverged at</Typography>
                <Typography sx={{ fontFamily: MONO, color: "warning.main", fontWeight: 600 }}>
                  {shortSha(blame.commit_sha)}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption">Last clean commit</Typography>
                <Typography sx={{ fontFamily: MONO, color: "success.main", fontWeight: 600 }}>
                  {shortSha(blame.previous_clean_sha)}
                </Typography>
              </Box>
            </Stack>
          )}

          {blame.commits.length > 0 && (
            <Box sx={{ overflowX: "auto" }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Commit</TableCell>
                    <TableCell align="right">Runs</TableCell>
                    <TableCell align="right">Pass</TableCell>
                    <TableCell align="right">Fail</TableCell>
                    <TableCell>Diverged</TableCell>
                    <TableCell align="right">First seen</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {blame.commits.map((window) => (
                    <TableRow key={window.commit_sha} hover>
                      <TableCell sx={{ fontFamily: MONO, fontSize: "0.78rem" }}>
                        {shortSha(window.commit_sha)}
                      </TableCell>
                      <TableCell align="right">{window.runs}</TableCell>
                      <TableCell align="right">{window.passes}</TableCell>
                      <TableCell align="right">{window.failures}</TableCell>
                      <TableCell>
                        {window.diverged ? (
                          <Typography variant="caption" sx={{ color: "warning.main", fontWeight: 600 }}>
                            yes
                          </Typography>
                        ) : window.observable ? (
                          <Typography variant="caption" sx={{ color: "success.main" }}>
                            no
                          </Typography>
                        ) : (
                          <Tooltip title="Ran only once at this commit, so divergence could not have been observed. Saying 'no' would imply evidence of stability that does not exist.">
                            <Typography variant="caption" color="text.secondary" sx={{ cursor: "help" }}>
                              unknown
                            </Typography>
                          </Tooltip>
                        )}
                      </TableCell>
                      <TableCell align="right">
                        <Typography variant="caption">{when(window.first_seen)}</Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </CardContent>
      </Card>

      <Grid container spacing={2.5}>
        {/* ---------- Action ---------- */}
        <Grid item xs={12} md={6}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Typography variant="h2">Action</Typography>
              <Typography variant="caption" sx={{ display: "block", mb: 2 }}>
                The dashboard is read-only. These are commands to run, so anything that
                changes state stays reviewable.
              </Typography>
              <Stack spacing={1}>
                {actions.map((action) => (
                  <Box
                    key={action.command}
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 1,
                      p: 1,
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: action.kind === "warn" ? "error.main" : "divider",
                      bgcolor: action.kind === "warn" ? "#f8717110" : "transparent",
                    }}
                  >
                    <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {action.label}
                      </Typography>
                      <Typography
                        sx={{
                          fontFamily: MONO,
                          fontSize: "0.72rem",
                          color: "text.secondary",
                          overflowWrap: "anywhere",
                        }}
                      >
                        {action.command}
                      </Typography>
                    </Box>
                    <CopyButton text={action.command} />
                  </Box>
                ))}
              </Stack>
            </CardContent>
          </Card>
        </Grid>

        {/* ---------- Neighbours / signatures ---------- */}
        <Grid item xs={12} md={6}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Typography variant="h2">
                {neighbours.length > 0 ? "What ran just before" : "Failure signatures"}
              </Typography>

              {neighbours.length > 0 ? (
                <>
                  <Typography variant="caption" sx={{ display: "block", mb: 1.5 }}>
                    Shown so the polluter verdict can be checked rather than believed. A
                    predecessor appearing equally before passes and failures explains nothing.
                  </Typography>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>Preceding test</TableCell>
                        <TableCell align="right">Before fail</TableCell>
                        <TableCell align="right">Before pass</TableCell>
                        <TableCell align="right">Share</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {neighbours.map((row) => (
                        <TableRow key={row.test_id} hover>
                          <TableCell sx={{ fontFamily: MONO, fontSize: "0.74rem" }}>
                            {leafName(row.test_id)}
                          </TableCell>
                          <TableCell align="right">{row.before_failure}</TableCell>
                          <TableCell align="right">{row.before_pass}</TableCell>
                          <TableCell
                            align="right"
                            sx={{ color: row.share >= 0.9 ? "warning.main" : "text.secondary" }}
                          >
                            {pct(row.share)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </>
              ) : signatures.length > 0 ? (
                <Stack spacing={1.25} sx={{ mt: 1 }}>
                  {signatures.map((entry) => (
                    <Box key={entry.signature}>
                      <Typography variant="caption" sx={{ color: "warning.main" }}>
                        {entry.count}×
                      </Typography>
                      <Typography
                        sx={{ fontFamily: MONO, fontSize: "0.74rem", overflowWrap: "anywhere" }}
                      >
                        {oneLine(entry.signature, 180)}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  No failures recorded.
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Stack>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Box>
      <Typography sx={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{value}</Typography>
      <Typography variant="caption">{label}</Typography>
    </Box>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <Tooltip title={copied ? "Copied" : "Copy command"}>
      <IconButton
        size="small"
        onClick={() => {
          // navigator.clipboard needs a secure context; localhost counts, but guard
          // anyway so the button never throws on an unusual setup.
          void navigator.clipboard
            ?.writeText(text)
            .then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1400);
            })
            .catch(() => setCopied(false));
        }}
      >
        <ContentCopyIcon sx={{ fontSize: 15 }} />
      </IconButton>
    </Tooltip>
  );
}
