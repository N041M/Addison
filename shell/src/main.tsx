import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { installCspViolationReporter } from "./lib/cspReport";
import "./styles.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root is missing from index.html");
}

// Before the app renders, so a violation raised while the first chunk loads is
// still captured. Never torn down — the window lives as long as the process does,
// and a CSP the app stops watching is a CSP nobody finds out about.
installCspViolationReporter();

createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
