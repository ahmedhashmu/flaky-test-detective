import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, HashRouter } from "react-router-dom";

import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider } from "@mui/material/styles";

import App from "./App";
import { theme } from "./lib/theme";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Missing #root element");
}

// A hash router only on a static host. `/tests/<id>` is a real path that `flaky serve`
// routes, but a plain file host has no such file and answers 404 -- so a deep link, or
// a refresh on a test page, would break. Hash routing keeps every route inside
// index.html. Local serving keeps clean paths, which is the better URL when something
// is actually listening.
const Router = window.__FTD_STATIC__ === true ? HashRouter : BrowserRouter;

createRoot(container).render(
  <StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Router>
        <App />
      </Router>
    </ThemeProvider>
  </StrictMode>,
);
