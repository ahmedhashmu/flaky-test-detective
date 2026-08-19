import { useCallback, useEffect, useState } from "react";
import { Link as RouterLink, Route, Routes } from "react-router-dom";

import RefreshIcon from "@mui/icons-material/Refresh";
import {
  AppBar,
  Box,
  Chip,
  Container,
  IconButton,
  Link,
  Toolbar,
  Tooltip,
  Typography,
} from "@mui/material";

import { EmptyState, ErrorState, Loading } from "./components/States";
import { Overview } from "./pages/Overview";
import { TestDetail } from "./pages/TestDetail";
import { fetchOverview } from "./lib/api";
import type { Overview as OverviewData } from "./lib/types";

const REFRESH_MS = 15_000;

export default function App() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      setData(await fetchOverview());
      setError(null);
    } catch (cause) {
      setError(cause);
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    // Polling rather than websockets: a local single-user viewer, and an ingest that
    // lands while the page is open should show up without a manual reload.
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <Box sx={{ minHeight: "100vh" }}>
      <AppBar
        position="sticky"
        elevation={0}
        sx={{
          bgcolor: "rgba(11,13,18,0.85)",
          backdropFilter: "blur(8px)",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Toolbar variant="dense" sx={{ gap: 2 }}>
          <Link
            component={RouterLink}
            to="/"
            underline="none"
            color="text.primary"
            sx={{ display: "flex", alignItems: "baseline", gap: 1 }}
          >
            <Typography sx={{ fontWeight: 700, letterSpacing: "-0.01em" }}>
              Flaky Test Detective
            </Typography>
            <Typography variant="caption" sx={{ display: { xs: "none", sm: "block" } }}>
              which failure actually matters
            </Typography>
          </Link>

          <Box sx={{ flexGrow: 1 }} />

          {data && (
            <Tooltip title="Read-only local view of .flaky.db">
              <Chip
                size="small"
                variant="outlined"
                label={`${data.summary.runs} runs · ${data.summary.tests} tests`}
              />
            </Tooltip>
          )}
          <Tooltip title="Refresh now">
            <IconButton size="small" onClick={() => void load()} disabled={refreshing}>
              <RefreshIcon
                sx={{
                  fontSize: 18,
                  animation: refreshing ? "spin 0.9s linear infinite" : "none",
                  "@keyframes spin": { to: { transform: "rotate(360deg)" } },
                }}
              />
            </IconButton>
          </Tooltip>
        </Toolbar>
      </AppBar>

      <Container maxWidth="xl" sx={{ py: 3 }}>
        {error && !data ? (
          <ErrorState error={error} />
        ) : !data ? (
          <Loading label="Analyzing run history" />
        ) : data.summary.runs === 0 ? (
          <EmptyState />
        ) : (
          <Routes>
            <Route path="/" element={<Overview data={data} />} />
            <Route path="/tests/:testId" element={<TestDetail />} />
            <Route path="*" element={<Overview data={data} />} />
          </Routes>
        )}
      </Container>

      <Box sx={{ py: 3, textAlign: "center" }}>
        <Typography variant="caption">
          Scores combine same-commit divergence (proof) with flip rate (inference), damped
          by run count. Root-cause categories are heuristics and show their evidence.
        </Typography>
      </Box>
    </Box>
  );
}
