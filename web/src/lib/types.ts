// Mirrors the payloads in src/flaky_detective/web/api.py.
//
// Hand-written rather than generated: the API is small, and a generated client would
// add a build step for no benefit. `api_version` is checked at runtime so a mismatch
// between this file and the server surfaces as a clear message rather than as
// undefined values scattered through the UI.

export type Tone = "error" | "warning" | "success" | "neutral";

export type Verdict = "flaky" | "regression" | "broken" | "fixed" | "stable";

export interface HealthComponent {
  name: string;
  detail: string;
  penalty: number;
  weight: number;
  healthy: boolean;
}

export interface WastedCi {
  seconds: number;
  minutes: number;
  flaky_failures: number;
  median_run_seconds: number;
  is_estimate: boolean;
  assumption: string;
}

export interface Trust {
  score: number;
  band: "healthy" | "fair" | "poor" | "critical";
  /** Exact points removed from 100. `score` is this, rounded. */
  deducted: number;
  components: HealthComponent[];
  facts: {
    total_tests: number;
    stable_tests: number;
    stable_share: number;
    active_flakes: number;
    unresolved_breaks: number;
    commit_coverage: number;
    quarantine_days_outstanding: number;
  };
  wasted_ci: WastedCi;
}

export interface TestSummary {
  test_id: string;
  name: string;
  suite: string | null;
  verdict: Verdict;
  tone: Tone;
  score: number;
  confidence: number;
  runs: number;
  passes: number;
  failures: number;
  skips: number;
  flips: number;
  failure_rate: number;
  divergent_commits: number;
  observed_commits: number;
  retries: number;
  cause: string | null;
  cause_confidence: number | null;
  polluter: string | null;
  last_seen: string | null;
  first_seen: string | null;
  last_status: string | null;
  signature_count: number;
}

export interface Cluster {
  signature: string;
  representative_message: string;
  test_ids: string[];
  test_count: number;
  failure_count: number;
  cause: string | null;
}

export interface Caveat {
  severity: "warning" | "info";
  title: string;
  detail: string;
}

export interface QuarantineEntry {
  test_id: string;
  reason: string;
  score: number;
  added_at: string;
  expires_at: string;
  days_remaining: number;
  expired: boolean;
}

export interface Overview {
  api_version: number;
  trust: Trust;
  summary: {
    runs: number;
    results: number;
    tests: number;
    flaky: number;
    regressions: number;
    broken: number;
    fixed: number;
    stable: number;
    has_commit_data: boolean;
    commit_coverage: number;
    threshold: number;
    window_start: string | null;
    window_end: string | null;
    runners: Record<string, number>;
  };
  tests: TestSummary[];
  clusters: Cluster[];
  quarantine: {
    available: boolean;
    path?: string;
    active: QuarantineEntry[];
    expired: QuarantineEntry[];
    recommended: {
      test_id: string;
      score: number;
      cause: string | null;
      failures: number;
      runs: number;
    }[];
  };
  caveats: Caveat[];
}

export interface EvidenceItem {
  label: string;
  detail: string;
}

export interface TimelinePoint {
  started_at: string | null;
  status: string;
  failed: boolean;
  commit_sha: string | null;
  branch: string | null;
  iteration: number | null;
  retried: boolean;
  duration: number | null;
  message: string | null;
  position: number | null;
}

export interface CommitWindow {
  commit_sha: string;
  runs: number;
  passes: number;
  failures: number;
  diverged: boolean;
  observable: boolean;
  first_seen: string | null;
}

export interface TestDetail {
  api_version: number;
  test: TestSummary;
  evidence: {
    proven: EvidenceItem[];
    inferred: EvidenceItem[];
    score_breakdown: {
      divergence_rate: number;
      flip_rate: number;
      confidence: number;
      score: number;
    };
  };
  timeline: TimelinePoint[];
  diagnosis: {
    cause: string;
    confidence: number;
    matched: string[];
    remediation: string;
    is_heuristic: boolean;
    order: {
      separation: number;
      mean_position_on_fail: number;
      mean_position_on_pass: number;
      likely_polluter: string | null;
      polluter_failure_share: number;
    } | null;
  } | null;
  blame: {
    attribution:
      | "introduced"
      | "predates_history"
      | "no_divergence"
      | "no_commit_data"
      | "too_sparse";
    actionable: boolean;
    commit_sha: string | null;
    previous_clean_sha: string | null;
    explanation: string;
    observable_commits: number;
    commits: CommitWindow[];
  };
  signatures: { signature: string; count: number; example: string | null }[];
  neighbours: {
    test_id: string;
    before_failure: number;
    before_pass: number;
    share: number;
  }[];
  quarantined: {
    reason: string;
    expires_at: string;
    days_remaining: number;
    expired: boolean;
  } | null;
  actions: { label: string; command: string; kind: string }[];
}
