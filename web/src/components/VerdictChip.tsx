import { Chip, Tooltip } from "@mui/material";

import { VERDICT_COLORS } from "../lib/theme";
import type { Verdict } from "../lib/types";

// Wording matches the CLI exactly. The vocabulary is fixed in
// .kiro/steering/product.md precisely so that "flaky" means one thing everywhere.
const EXPLANATION: Record<Verdict, string> = {
  flaky: "Different outcomes for the same code. Proven where same-commit divergence exists.",
  regression:
    "Consistent failure that used to pass, and the streak is longer than this test's own history explains. Needs a human, not a re-run.",
  broken: "Has never passed in recorded history. Usually an incomplete commit rather than a break.",
  fixed: "Was flaky, now stable for a full streak of passes.",
  stable: "No failures recorded.",
};

export function VerdictChip({ verdict, size = "small" }: { verdict: Verdict; size?: "small" | "medium" }) {
  const color = VERDICT_COLORS[verdict] ?? VERDICT_COLORS.stable;

  return (
    <Tooltip title={EXPLANATION[verdict]}>
      <Chip
        label={verdict}
        size={size}
        sx={{
          bgcolor: `${color}1f`,
          color,
          border: `1px solid ${color}44`,
          minWidth: 78,
        }}
      />
    </Tooltip>
  );
}
