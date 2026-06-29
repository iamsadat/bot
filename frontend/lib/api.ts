// Typed client for the JobHunt FastAPI backend. Same-origin in production
// (served under /app); set NEXT_PUBLIC_API_BASE for local dev against :8000.

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '';

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`);
  const ct = res.headers.get('content-type') || '';
  return (ct.includes('application/json') ? res.json() : (res.text() as any)) as T;
}

export const api = {
  status: () => req<Status>('GET', '/api/status'),
  jobs: () => req<{ jobs: Job[] }>('GET', '/api/jobs'),
  approvals: (filter = 'pending') =>
    req<{ approvals: Approval[] }>('GET', `/api/approvals?state_filter=${filter}`),
  activity: (limit = 80) =>
    req<{ activity: ActivityEvent[]; grouped: Record<string, ActivityEvent[]> }>(
      'GET', `/api/activity?limit=${limit}`),
  autonomy: () => req<Autonomy>('GET', '/api/autonomy'),
  setAutonomy: (b: Partial<Autonomy>) => req<any>('POST', '/api/autonomy', b),
  document: (jobId: string) => req<{ document: Doc }>('GET', `/api/documents/${jobId}`),
  profile: () => req<{ profile: Profile | null }>('GET', '/api/profile'),
  parseResume: (text: string) => req<ParsedResume>('POST', '/api/onboarding/resume', { text }),
  parseResumeFile: (filename: string, content_base64: string) =>
    req<ParsedResume>('POST', '/api/profile/parse-resume-file', { filename, content_base64 }),
  importGithub: (username: string) =>
    req<{ added: number; projects: any[] }>('POST', '/api/profile/import-github', { username }),
  saveProfile: (p: any) => req<any>('POST', '/api/onboarding/profile', p),
  saveStructured: (p: any) => req<any>('PUT', '/api/profile/structured', p),
  startHunt: () => req<any>('POST', '/api/hunt/start'),
  discover: () => req<any>('POST', '/api/discover'),
  approve: (id: string, decision = 'approve') =>
    req<any>('POST', `/api/approve/${id}?decision=${decision}`),
  downloadUrl: (jobId: string, fmt: string, kind = 'resume') =>
    `${API_BASE}/api/documents/${jobId}/download?format=${fmt}&kind=${kind}`,
  salary: (role: string, location = '') =>
    req<{ currency: string; p10: number; median: number; p90: number; sample: number }>(
      'GET', `/api/salary?role=${encodeURIComponent(role)}&location=${encodeURIComponent(location)}`),
};

export function wsUrl(): string {
  const base = API_BASE || (typeof window !== 'undefined' ? window.location.origin : '');
  return base.replace(/^http/, 'ws') + '/ws/stream';
}

// ---- types ---------------------------------------------------------------
export interface Status {
  hunt_status: string; jobs_count: number; applied_count: number;
  approvals_pending: number; ats_configured: boolean; has_profile: boolean;
  auto_apply: boolean; applied_today: number; continuous: boolean;
  inbox_connected: boolean; llm?: { provider?: string };
}
export interface Job {
  job_id: string; title: string; company: string; location: string; url: string;
  status: string; relevance_score?: number; remote?: boolean; submitted?: boolean;
  events?: { ts: number; stage: string; detail: string; status: string }[];
}
export interface Approval {
  request_id: string; job_id: string; company: string; title: string; state: string;
}
export interface ActivityEvent {
  agent: string; task_id: string; thought: string; phase?: string;
  considered?: string[]; rejected?: { item: string; reason: string }[];
  confidence?: number | null; decision?: string;
}
export interface Autonomy {
  auto_apply: boolean; daily_apply_cap: number; relevance_floor: number;
  ats_connected: boolean; applied_today: number; effective_cap: number; continuous: boolean;
}
export interface Doc {
  job_id: string; company: string; title: string; draft?: ResumeDraft | null;
  keyword_coverage?: number; matched_keywords?: string[]; missing_keywords?: string[];
}
export interface ResumeDraft {
  candidate_name: string; candidate_email: string; phone?: string; location?: string;
  links?: Record<string, string>; summary: string; sections: ResumeSection[];
  matched_keywords: string[]; missing_keywords: string[];
}
export interface ResumeSection {
  title: string; kind: string; body?: string;
  rows?: { left: string; right?: string; link?: string; bullets?: { text: string }[] }[];
}
export interface Profile {
  name: string; email: string; phone?: string; skills: string[];
  experiences: any[]; education: any[]; projects: any[]; links: Record<string, string>;
}
export interface ParsedResume {
  skills: string[]; experiences: any[]; education: any[]; projects: any[];
  links: Record<string, string>;
}
