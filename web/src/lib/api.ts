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

export const fetchOverview = () => request<Overview>("/api/overview");

export const fetchTestDetail = (testId: string) =>
  request<TestDetail>(`/api/tests/${encodeURIComponent(testId)}`);
