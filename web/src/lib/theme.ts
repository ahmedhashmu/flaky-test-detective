import { createTheme } from "@mui/material/styles";

// A CI command center, not a generic analytics template. Dark by default because it
// sits next to a terminal, and the palette is deliberately narrow: verdict colour is
// information here, so decorative colour elsewhere would dilute it.

export const VERDICT_COLORS = {
  flaky: "#fbbf24",
  regression: "#f87171",
  broken: "#fca5a5",
  fixed: "#4ade80",
  stable: "#64748b",
} as const;

export const TONE_COLORS = {
  error: "#f87171",
  warning: "#fbbf24",
  success: "#4ade80",
  neutral: "#64748b",
} as const;

export const theme = createTheme({
  palette: {
    mode: "dark",
    background: { default: "#0b0d12", paper: "#12151c" },
    primary: { main: "#60a5fa" },
    secondary: { main: "#fbbf24" },
    error: { main: "#f87171" },
    warning: { main: "#fbbf24" },
    success: { main: "#4ade80" },
    divider: "#232936",
    text: { primary: "#e5e7eb", secondary: "#94a3b8" },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily:
      'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif',
    fontSize: 14,
    h1: { fontSize: "1.5rem", fontWeight: 600 },
    h2: { fontSize: "1.2rem", fontWeight: 600 },
    h3: { fontSize: "1rem", fontWeight: 600 },
    // Used for every score, count and rate, so columns of numbers line up.
    body2: { fontSize: "0.875rem" },
    caption: { fontSize: "0.75rem", color: "#94a3b8" },
    button: { textTransform: "none", fontWeight: 500 },
  },
  components: {
    MuiPaper: {
      styleOverrides: {
        root: { backgroundImage: "none", border: "1px solid #1e2430" },
      },
    },
    MuiCard: { defaultProps: { elevation: 0 } },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: "#1e2430" },
        head: {
          color: "#94a3b8",
          fontSize: "0.7rem",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          fontWeight: 600,
        },
      },
    },
    MuiChip: {
      styleOverrides: { root: { fontWeight: 600, fontSize: "0.72rem" } },
    },
    MuiTooltip: {
      defaultProps: { arrow: true },
      styleOverrides: {
        tooltip: { fontSize: "0.78rem", maxWidth: 380, lineHeight: 1.5 },
      },
    },
    MuiCssBaseline: {
      styleOverrides: {
        // Monospace anywhere a test id, commit SHA or failure message appears: they
        // are identifiers, and proportional fonts make them harder to compare.
        code: {
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
          fontSize: "0.82em",
        },
      },
    },
  },
});

export const MONO =
  'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
