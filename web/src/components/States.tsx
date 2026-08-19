import { Alert, AlertTitle, Box, CircularProgress, Stack, Typography } from "@mui/material";

import { ApiError } from "../lib/api";
import { MONO } from "../lib/theme";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <Stack alignItems="center" spacing={2} sx={{ py: 10 }}>
      <CircularProgress size={26} />
      <Typography variant="body2" color="text.secondary">
        {label}…
      </Typography>
    </Stack>
  );
}

/**
 * Errors say what to do next.
 *
 * "Failed to fetch" is useless on its own; the most likely causes here are that the
 * server stopped or that no runs have been recorded yet, and both have a specific fix.
 */
export function ErrorState({ error }: { error: unknown }) {
  const isApi = error instanceof ApiError;
  const message = isApi ? error.message : String(error);
  const hint = isApi ? error.hint : undefined;

  return (
    <Box sx={{ py: 6, maxWidth: 640, mx: "auto" }}>
      <Alert severity="error" variant="outlined">
        <AlertTitle>Cannot load the dashboard</AlertTitle>
        <Typography variant="body2" sx={{ mb: hint ? 1.5 : 0 }}>
          {message}
        </Typography>
        {hint && (
          <Typography
            variant="body2"
            sx={{ fontFamily: MONO, fontSize: "0.78rem", color: "text.secondary" }}
          >
            {hint}
          </Typography>
        )}
      </Alert>
    </Box>
  );
}

/** Shown when the database exists but holds nothing yet. */
export function EmptyState() {
  return (
    <Box sx={{ py: 8, textAlign: "center", maxWidth: 560, mx: "auto" }}>
      <Typography variant="h2" sx={{ mb: 1 }}>
        No runs recorded yet
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Flakiness is only visible across runs, so there is nothing to show until a suite
        has run more than once.
      </Typography>
      <Box
        sx={{
          textAlign: "left",
          p: 2,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
          fontFamily: MONO,
          fontSize: "0.8rem",
        }}
      >
        <Box sx={{ color: "text.secondary" }}># provoke flakes locally</Box>
        <Box>flaky hunt -n 20 -- pytest tests/</Box>
        <Box sx={{ mt: 1.5, color: "text.secondary" }}># or ingest what CI already produced</Box>
        <Box>flaky ingest 'reports/**/*.xml'</Box>
      </Box>
    </Box>
  );
}
