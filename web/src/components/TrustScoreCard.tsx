import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import {
  Box,
  Card,
  CardContent,
  Chip,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";

import { duration, pct } from "../lib/format";
import type { Trust } from "../lib/types";

const BAND_COLOR: Record<Trust["band"], string> = {
  healthy: "#4ade80",
  fair: "#fbbf24",
  poor: "#fb923c",
  critical: "#f87171",
};

/**
 * The headline answer to "can I trust my CI right now".
 *
 * Every deducted point is listed with the component that took it, because a single
 * opaque number would be exactly the kind of unexaminable verdict this tool exists to
 * replace. The penalties below sum to `trust.deducted`; the headline figure is that
 * total taken off 100 and rounded, which is why the arithmetic is shown rather than
 * left for the reader to attempt and get half a point wrong.
 */
export function TrustScoreCard({ trust }: { trust: Trust }) {
  const color = BAND_COLOR[trust.band];
  const { facts, wasted_ci: wasted } = trust;

  return (
    <Card sx={{ height: "100%" }}>
      <CardContent>
        <Stack direction="row" alignItems="baseline" spacing={1.5}>
          <Typography variant="overline" color="text.secondary">
            CI Trust Score
          </Typography>
          <Tooltip
            title="Built only from figures already collected, never a fitted model. Every point deducted is attributed to a named component below, and those penalties account for the whole deduction."
          >
            <HelpOutlineIcon sx={{ fontSize: 15, color: "text.secondary" }} />
          </Tooltip>
        </Stack>

        <Stack direction="row" alignItems="flex-end" spacing={1.5} sx={{ mt: 1 }}>
          <Typography sx={{ fontSize: "3.4rem", fontWeight: 700, lineHeight: 1, color }}>
            {trust.score}
          </Typography>
          <Typography variant="h3" color="text.secondary" sx={{ pb: 0.75 }}>
            / 100
          </Typography>
          <Chip
            label={trust.band}
            size="small"
            sx={{ mb: 1, bgcolor: `${color}22`, color, textTransform: "capitalize" }}
          />
        </Stack>

        <LinearProgress
          variant="determinate"
          value={trust.score}
          sx={{
            mt: 1.5,
            height: 6,
            borderRadius: 3,
            bgcolor: "#1e2430",
            "& .MuiLinearProgress-bar": { bgcolor: color, borderRadius: 3 },
          }}
        />

        <Stack spacing={1.25} sx={{ mt: 2.5 }}>
          {trust.components.map((component) => (
            <Box key={component.name}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  {component.name}
                </Typography>
                <Typography
                  variant="body2"
                  sx={{
                    fontVariantNumeric: "tabular-nums",
                    color: component.healthy ? "success.main" : "warning.main",
                    fontWeight: 600,
                  }}
                >
                  {component.healthy ? "ok" : `−${component.penalty.toFixed(1)}`}
                </Typography>
              </Stack>
              <Typography variant="caption" sx={{ display: "block", lineHeight: 1.45 }}>
                {component.detail}
              </Typography>
            </Box>
          ))}
        </Stack>

        <Typography
          variant="caption"
          sx={{ display: "block", mt: 1.5, fontVariantNumeric: "tabular-nums" }}
        >
          100 − {trust.deducted.toFixed(1)} deducted = {trust.score}
        </Typography>

        <Box sx={{ mt: 2.5, pt: 2, borderTop: "1px solid", borderColor: "divider" }}>
          <Stack direction="row" flexWrap="wrap" useFlexGap spacing={2}>
            <Fact label="Stable tests" value={`${pct(facts.stable_share, 1)}`} />
            <Fact label="Active flakes" value={String(facts.active_flakes)} />
            <Fact label="Unresolved breaks" value={String(facts.unresolved_breaks)} />
            <Fact label="Commit evidence" value={pct(facts.commit_coverage)} />
          </Stack>

          {wasted.median_run_seconds > 0 && (
            <Tooltip title={wasted.assumption}>
              <Stack
                direction="row"
                spacing={0.75}
                alignItems="center"
                sx={{ mt: 2, cursor: "help", width: "fit-content" }}
              >
                <Typography variant="body2" sx={{ color: "warning.main", fontWeight: 600 }}>
                  ≈ {duration(wasted.seconds)}
                </Typography>
                <Typography variant="caption">
                  CI time lost to {wasted.flaky_failures} flaky failures (estimate)
                </Typography>
                <HelpOutlineIcon sx={{ fontSize: 13, color: "text.secondary" }} />
              </Stack>
            </Tooltip>
          )}
        </Box>
      </CardContent>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ minWidth: 96 }}>
      <Typography sx={{ fontSize: "1.05rem", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      <Typography variant="caption">{label}</Typography>
    </Box>
  );
}
