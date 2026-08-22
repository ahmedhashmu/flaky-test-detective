// Thin fetch wrapper.
//
// No data-fetching library: two endpoints do not justify one, and keeping the
// dependency list short matters as much here as it does in the Python package.

import type { Overview, TestDetail } from "./types";

const EXPECTED_API_VERSION = 1;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly hint?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Static mode: the same dashboard, served from a plain file host with no Python
// process behind it. The published sample site uses it so a reviewer can click
// through real output without installing anything.
//
// Gated on a flag the static index.html sets before the bundle loads, so `flaky serve`
// takes the live path unchanged. A build-time switch would have meant two bundles, and
// the bundle committed to the package is checked against a rebuild in CI -- one of them
// would inevitably go stale.
declare global {
  interface Window {
    __FTD_STATIC__?: boolean;
  }
}

const isStatic = (): boolean =>
  typeof window !== "undefined" && window.__FTD_STATIC__ === true;

let staticDetails: Promise<Record<string, TestDetail>> | null = null;

/** Every test's detail payload in one file, because a static host cannot route. */
function loadStaticDetails(): Promise<Record<string, TestDetail>> {
  staticDetails ??= request<StaticDetails>("./api/tests.json").then((payload) => payload.tests);
  return staticDetails;
}

interface StaticDetails {
  api_version?: number;
  tests: Record<string, TestDetail>;
}

async function request<T extends { api_version?: number }>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError(
      "Cannot reach the flaky-test-detective server.",
      0,
      "Is `flaky serve` still running?",
    );
  }

  if (!response.ok) {
    let detail = `Request failed with ${response.status}`;
    let hint: string | undefined;
    try {
      const body = (await response.json()) as { error?: string };
      if (body.error) {
        detail = body.error;
      }
    } catch {
      // A non-JSON error body is not worth reporting verbatim.
    }
    if (response.status === 503) {
      hint = "Record some runs first: `flaky hunt -n 20 -- pytest tests/`";
    }
    throw new ApiError(detail, response.status, hint);
  }

  const payload = (await response.json()) as T;

  // A silent version mismatch would show up as blank panels rather than an error,
  // which is much harder to diagnose than saying so plainly.
  if (payload.api_version !== undefined && payload.api_version !== EXPECTED_API_VERSION) {
    throw new ApiError(
      `Dashboard expects API version ${EXPECTED_API_VERSION}, server sent ${payload.api_version}.`,
      500,
      "The dashboard bundle and the installed package are out of step. Rebuild with `npm run build` in web/.",
    );
  }

  return payload;
}

export const fetchOverview = () =>
  isStatic() ? request<Overview>("./api/overview.json") : request<Overview>("/api/overview");

export const fetchTestDetail = async (testId: string): Promise<TestDetail> => {
  if (!isStatic()) {
    return request<TestDetail>(`/api/tests/${encodeURIComponent(testId)}`);
  }

  const detail = (await loadStaticDetails())[testId];
  if (!detail) {
    // Matches the live server's 404 shape, so the same error UI covers both.
    throw new ApiError(`No test matching ${testId}`, 404);
  }
  return detail;
};
