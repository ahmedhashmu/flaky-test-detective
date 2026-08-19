import { Box, Stack, Tooltip, Typography } from "@mui/material";

import { oneLine, shortSha, when } from "../lib/format";
import type { TimelinePoint } from "../lib/types";

/**
 * Green and red squares, one per run, oldest first.
 *
 * Runs at the same commit are grouped and labelled, because a group containing both a
 * pass and a fail *is* the proof of flakiness -- the point is to make that visible at a
 * glance rather than described in a sentence.
 */
export function RunTimeline({ timeline }: { timeline: TimelinePoint[] }) {
  const groups = groupByCommit(timeline);

  return (
    <Box>
      <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1.5}>
        {groups.map((group, index) => {
          const diverged = group.hasPass && group.hasFail;
          return (
            <Box key={`${group.commit ?? "none"}-${index}`}>
              <Stack
                direction="row"
                spacing={0.5}
                sx={{
                  p: 0.5,
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: diverged ? "warning.main" : "transparent",
                  bgcolor: diverged ? "#fbbf2410" : "transparent",
                }}
              >
                {group.points.map((point, pointIndex) => (
                  <Tooltip
                    key={pointIndex}
                    title={
                      <Box>
                        <Typography variant="caption" sx={{ fontWeight: 600 }}>
                          {point.status}
                          {point.retried ? " (runner retried)" : ""}
                        </Typography>
                        <Typography variant="caption" sx={{ display: "block" }}>
                          {when(point.started_at)}
                        </Typography>
                        {point.commit_sha && (
                          <Typography variant="caption" sx={{ display: "block" }}>
                            commit {shortSha(point.commit_sha)}
                          </Typography>
                        )}
                        {point.message && (
                          <Typography variant="caption" sx={{ display: "block", mt: 0.5 }}>
                            {oneLine(point.message, 220)}
                          </Typography>
                        )}
                      </Box>
                    }
                  >
                    <Box
                      sx={{
                        width: 13,
                        height: 22,
                        borderRadius: 0.5,
                        cursor: "help",
                        bgcolor: point.retried
                          ? "#fbbf24"
                          : point.failed
                            ? "#f87171"
                            : point.status === "skipped"
                              ? "#334155"
                              : "#4ade80",
                      }}
                    />
                  </Tooltip>
                ))}
              </Stack>
              {group.commit && (
                <Typography
                  variant="caption"
                  sx={{
                    display: "block",
                    textAlign: "center",
                    mt: 0.25,
                    fontSize: "0.62rem",
                    color: diverged ? "warning.main" : "text.secondary",
                  }}
                >
                  {shortSha(group.commit)}
                </Typography>
              )}
            </Box>
          );
        })}
      </Stack>

      <Stack direction="row" spacing={2} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap>
        <Legend color="#4ade80" label="passed" />
        <Legend color="#f87171" label="failed" />
        <Legend color="#fbbf24" label="runner retried" />
        <Legend color="#334155" label="skipped" />
        <Stack direction="row" spacing={0.75} alignItems="center">
          <Box
            sx={{
              width: 13,
              height: 13,
              border: "1px solid",
              borderColor: "warning.main",
              borderRadius: 0.5,
              bgcolor: "#fbbf2410",
            }}
          />
          <Typography variant="caption">both outcomes at one commit — proof</Typography>
        </Stack>
      </Stack>
    </Box>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <Stack direction="row" spacing={0.75} alignItems="center">
      <Box sx={{ width: 13, height: 13, borderRadius: 0.5, bgcolor: color }} />
      <Typography variant="caption">{label}</Typography>
    </Stack>
  );
}

interface CommitGroup {
  commit: string | null;
  points: TimelinePoint[];
  hasPass: boolean;
  hasFail: boolean;
}

/** Consecutive runs sharing a commit, in chronological order. */
function groupByCommit(timeline: TimelinePoint[]): CommitGroup[] {
  const groups: CommitGroup[] = [];

  for (const point of timeline) {
    const last = groups[groups.length - 1];
    if (last && last.commit === point.commit_sha) {
      last.points.push(point);
      last.hasPass ||= !point.failed && point.status !== "skipped";
      last.hasFail ||= point.failed || point.retried;
      continue;
    }
    groups.push({
      commit: point.commit_sha,
      points: [point],
      hasPass: !point.failed && point.status !== "skipped",
      hasFail: point.failed || point.retried,
    });
  }

  return groups;
}
