'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Nav from '@/components/Nav';
import Rail from '@/components/Rail';
import { Button, Card, Field, Input, Meter, PageHead, SectionHead, Select, Tag, Textarea } from '@/components/ui';
import { api, AtsConfig } from '@/lib/api';
import { usePoll } from '@/lib/useLive';

type Exp = { title: string; company: string; location: string; start: string; end: string; bullets: string[] };
type Edu = { school: string; degree: string; end: string };
type Proj = { name: string; link: string; bullets: string[] };

// Standard answers to the screening questions application forms ask. The keys
// are the ones the submitters and autofill look up; any other key already
// stored in application_answers is carried through a save untouched.
type AnswerField = { key: string; label: string; placeholder?: string; yesNo?: boolean };
const SCREENING_FIELDS: AnswerField[] = [
  { key: 'work_authorization', label: 'Work authorization', placeholder: 'Indian citizen — no sponsorship needed' },
  { key: 'requires_sponsorship', label: 'Need visa sponsorship?', yesNo: true },
  { key: 'years_experience', label: 'Years of experience', placeholder: 'e.g. 3' },
  { key: 'notice_period', label: 'Notice period', placeholder: 'e.g. 30 days, or Immediate joiner' },
  { key: 'current_ctc', label: 'Current CTC', placeholder: 'e.g. ₹12 LPA' },
  { key: 'expected_ctc', label: 'Expected CTC', placeholder: 'e.g. ₹18 LPA' },
  { key: 'willing_to_relocate', label: 'Willing to relocate?', yesNo: true },
  { key: 'how_did_you_hear', label: 'How did you hear about the role?', placeholder: 'e.g. LinkedIn, company careers page' },
  { key: 'linkedin', label: 'LinkedIn URL', placeholder: 'https://linkedin.com/in/…' },
  { key: 'github', label: 'GitHub URL', placeholder: 'https://github.com/…' },
  { key: 'website', label: 'Portfolio / website', placeholder: 'https://…' },
];
const SELF_ID_FIELDS: AnswerField[] = [
  { key: 'gender', label: 'Gender' },
  { key: 'race', label: 'Race / ethnicity' },
  { key: 'veteran_status', label: 'Veteran status' },
  { key: 'disability_status', label: 'Disability status' },
];

// Older profiles may hold true/false or "yes"/"no"; show them in the Yes/No picker.
function normalizeAnswers(raw?: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...(raw || {}) };
  for (const f of SCREENING_FIELDS) {
    if (!f.yesNo) continue;
    const v = out[f.key];
    if (v === true || (typeof v === 'string' && /^(y|yes|true)$/i.test(v.trim()))) out[f.key] = 'Yes';
    else if (v === false || (typeof v === 'string' && /^(n|no|false)$/i.test(v.trim()))) out[f.key] = 'No';
  }
  return out;
}

const splitList = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean);

export default function Onboarding() {
  const router = useRouter();
  const [placeholders, setPlaceholders] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [roles, setRoles] = useState('');
  const [locations, setLocations] = useState('');
  const [skills, setSkills] = useState<string>('');
  const [exps, setExps] = useState<Exp[]>([]);
  const [edu, setEdu] = useState<Edu[]>([]);
  const [projs, setProjs] = useState<Proj[]>([]);
  const [links, setLinks] = useState<Record<string, string>>({});
  const [paste, setPaste] = useState('');
  const [msg, setMsg] = useState('');
  const [ghTokens, setGhTokens] = useState('');
  const [leverSlugs, setLeverSlugs] = useState('');
  const [ashbySlugs, setAshbySlugs] = useState('');
  // The discovery-only boards (recruitee/workable/personio) aren't edited
  // here, but the ATS endpoint replaces the whole config — keep them to send back.
  const [atsRest, setAtsRest] = useState<AtsConfig>({});
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  // Decides create (POST, first run only) vs merge-update (PUT) on save.
  const [hasProfile, setHasProfile] = useState(false);

  // Real "evidence coverage" figure — same endpoint the Tracker screen uses —
  // stands in for the mockup's per-job ATS coverage meter, which needs a
  // tailored job draft that doesn't exist yet at onboarding time.
  const metrics = usePoll(() => api.metrics(), 8000);

  // Assistant hook: re-run the profile load below when the in-app assistant
  // changes something (it dispatches `jobhunt:refresh`).
  const [refreshTick, setRefreshTick] = useState(0);
  useEffect(() => {
    const bump = () => setRefreshTick((n) => n + 1);
    window.addEventListener('jobhunt:refresh', bump);
    return () => window.removeEventListener('jobhunt:refresh', bump);
  }, []);

  useEffect(() => {
    api.profile().then((r) => {
      const a = r.ats_config;
      if (a) {
        const { greenhouse_tokens = [], lever_slugs = [], ashby_slugs = [], ...rest } = a;
        setGhTokens(greenhouse_tokens.join(', '));
        setLeverSlugs(lever_slugs.join(', '));
        setAshbySlugs(ashby_slugs.join(', '));
        setAtsRest(rest);
      }
      const p = r.profile;
      if (!p) {
        // Uploaded but never saved: bring the parse back instead of a blank form.
        if (r.pending_parse) applyParsed(r.pending_parse);
        return;
      }
      setHasProfile(true);
      setName(p.name || ''); setEmail(p.email || ''); setPhone(p.phone || '');
      setRoles((p.target_roles || []).join(', '));
      setLocations((p.locations || []).join(', '));
      setAnswers(normalizeAnswers(p.application_answers));
      setSkills((p.skills || []).join(', '));
      setExps((p.experiences || []) as Exp[]);
      setEdu((p.education || []) as Edu[]);
      setProjs((p.projects || []) as Proj[]);
      setLinks(p.links || {});
    }).catch(() => {});
  }, [refreshTick]);

  const applyParsed = (r: any) => {
    // Contact details and a first target role fill only empty fields, so a
    // re-upload never overwrites what was typed.
    const c = r.contact || {};
    if (c.name) setName((v) => v || c.name);
    if (c.email) setEmail((v) => v || c.email);
    if (c.phone) setPhone((v) => v || c.phone);
    if (r.inferred_titles?.length) setRoles((v) => v || r.inferred_titles[0]);
    setPlaceholders(r.placeholders || []);
    if (r.skills?.length) setSkills((s) => Array.from(new Set([...s.split(',').map((x: string) => x.trim()).filter(Boolean), ...r.skills])).join(', '));
    if (r.experiences?.length) setExps(r.experiences as Exp[]);
    if (r.education?.length) setEdu(r.education as Edu[]);
    if (r.projects?.length) setProjs(r.projects as Proj[]);
    if (r.links) setLinks((l) => ({ ...r.links, ...l }));
  };

  const parse = async () => {
    if (!paste.trim()) return;
    setMsg('Parsing…');
    try {
      applyParsed(await api.parseResume(paste));
      setMsg('Parsed — review & edit below.');
    } catch {
      setMsg('Parse failed.');
    }
  };

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setMsg('Reading file…');
    try {
      const buf = await file.arrayBuffer();
      let bin = '';
      const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      applyParsed(await api.parseResumeFile(file.name, btoa(bin)));
      setMsg('Parsed file — review & edit below.');
    } catch {
      setMsg('Could not parse that file (try .docx/.pdf/.txt).');
    }
  };

  const normalizeGithubUser = (raw: string) => {
    let u = raw.trim();
    u = u.replace(/^https?:\/\//i, '').replace(/^www\./i, '').replace(/^github\.com\//i, '').replace(/^@/, '');
    u = u.split('?')[0];
    u = u.replace(/^\/+|\/+$/g, '');
    return u.split('/')[0].trim();
  };

  const importGithub = async () => {
    const u = normalizeGithubUser(links.github || '');
    if (!u) { setMsg('Enter your GitHub (in Links) first.'); return; }
    setMsg('Importing GitHub…');
    try {
      const r = await api.importGithub(u);
      if (r.projects?.length) setProjs(r.projects as Proj[]);
      setMsg(`Imported ${r.added} project(s) from GitHub.`);
    } catch (e) {
      setMsg((e as Error).message || 'GitHub import failed.');
    }
  };

  const answerValue = (key: string) => {
    const v = answers[key];
    return v == null ? '' : String(v);
  };
  const setAnswer = (key: string, value: string) => setAnswers((a) => ({ ...a, [key]: value }));

  const save = async (): Promise<boolean> => {
    const target_roles = splitList(roles);
    const missing = [
      !name.trim() && 'full name',
      !email.includes('@') && 'email',
      !target_roles.length && 'a target role',
    ].filter(Boolean);
    if (missing.length) {
      setMsg(`Can't save yet — add ${missing.join(', ')} under Basics.`);
      document.getElementById('targets')?.scrollIntoView({ behavior: 'smooth' });
      return false;
    }
    setSaving(true);
    setMsg('Saving…');
    // Every stored key goes back (unknown ones included); the server drops
    // blank values, so clearing a field removes that answer.
    const application_answers = Object.fromEntries(
      Object.entries(answers).map(([k, v]) => [k, typeof v === 'string' ? v.trim() : v]),
    );
    const basics = {
      name, email, phone, target_roles,
      locations: splitList(locations),
      skills: splitList(skills),
      links,
    };
    try {
      if (hasProfile) {
        // Merge-update: auto-apply, radar settings and anything else this
        // form doesn't show are left as they are.
        await api.updateProfile({ ...basics, application_answers });
      } else {
        // First run only — the POST builds a fresh profile and doesn't take
        // screening answers, so they follow in a merge-update.
        await api.saveProfile(basics);
        setHasProfile(true);
        if (Object.keys(application_answers).length) await api.updateProfile({ application_answers });
      }
      await api.saveStructured({ experiences: exps, education: edu, projects: projs, links });
      await api.saveAts({
        ...atsRest,
        greenhouse_tokens: splitList(ghTokens),
        lever_slugs: splitList(leverSlugs),
        ashby_slugs: splitList(ashbySlugs),
      });
      setMsg('Saved ✓');
      return true;
    } catch (e) {
      setMsg(`Save failed — ${(e as Error).message || 'check name/email/role.'}`);
      return false;
    } finally {
      setSaving(false);
    }
  };

  // The dashboard sends anyone without a saved profile back here, so leaving
  // without saving looped onto an empty form. Save first; leave only if it took.
  const saveAndOpenDashboard = async () => {
    if (await save()) router.push('/dashboard');
  };

  const saveButton = (
    <Button variant="primary" onClick={() => { void save(); }} disabled={saving}>
      {saving ? 'Saving…' : 'Save profile'}
    </Button>
  );

  // Bullets are the closest real analogue to the mockup's "claims you can
  // back up" — every one is something a hunt agent can point a résumé line
  // at. Derived from state already in memory; no new fetch.
  const claimCount =
    exps.reduce((n, e) => n + (e.bullets || []).filter((b) => b.trim()).length, 0) +
    projs.reduce((n, p) => n + (p.bullets || []).filter((b) => b.trim()).length, 0);

  const skillChips = skills.split(',').map((s) => s.trim()).filter(Boolean);
  const coverage = metrics?.evidence_coverage ?? 0;

  return (
    <>
      <Rail />
      <div className="lg:pl-[220px]">
        <div className="lg:hidden">
          <Nav right={saveButton} />
        </div>

        <main className="relative z-10 mx-auto min-h-screen max-w-[1180px]">
          <div className="space-y-5 px-6 pb-16 pt-6">
            <PageHead
              title="Your history"
              subtitle="Everything the agents are allowed to claim about you lives here."
              actions={
                <div className="flex items-center gap-1.5">
                  <Tag tone="solid">1 · History</Tag>
                  <Tag tone="neutral">2 · Targets</Tag>
                  <Tag tone="neutral">3 · Boards</Tag>
                  <Tag tone="neutral">4 · Screening</Tag>
                </div>
              }
            />

            <div className="grid gap-4 lg:grid-cols-[290px_minmax(0,1fr)_330px]">
              {/* Left — résumé intake */}
              <div className="flex flex-col gap-3">
                <label
                  className="block cursor-pointer rounded-xl2 border border-dashed p-5 text-center"
                  style={{ borderColor: 'color-mix(in srgb, var(--color-text) 28%, transparent)', background: 'var(--color-surface)' }}
                >
                  <div
                    className="mx-auto mb-3 grid h-[52px] w-[52px] place-items-center rounded-full"
                    style={{ background: 'var(--color-bg)' }}
                  >
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--color-accent)" strokeWidth="2.75" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 17V5" />
                      <path d="M7 10l5-5 5 5" />
                      <path d="M4 19h16" />
                    </svg>
                  </div>
                  <div className="text-sm font-semibold">Drop a résumé</div>
                  <div className="mt-1 text-[11.5px] leading-snug text-muted">
                    PDF, DOCX or plain text. We parse it into the structured builder.
                  </div>
                  <input
                    type="file" accept=".pdf,.docx,.txt" className="hidden"
                    onChange={(e) => onFile(e.target.files?.[0])}
                  />
                </label>

                <Button variant="secondary" block onClick={importGithub}>Import from GitHub</Button>

                <Field label="Or paste résumé text">
                  <Textarea
                    value={paste} onChange={(e) => setPaste(e.target.value)}
                    rows={4} placeholder="Paste your résumé text…"
                  />
                </Field>
                <Button variant="secondary" block onClick={parse}>Parse &amp; prefill</Button>
                {msg && <span className="text-xs text-muted">{msg}</span>}

                <Card elevation="none" className="mt-1" style={{ background: 'var(--color-accent-2-100)' }}>
                  <div className="text-[11px] uppercase tracking-[0.09em]" style={{ color: 'var(--color-accent-2-700)' }}>
                    Evidence graph
                  </div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-[32px] leading-[1.1]" style={{ fontFamily: 'var(--font-heading)' }}>
                      {claimCount}
                    </span>
                    <span className="text-xs text-muted">claim{claimCount === 1 ? '' : 's'} you can back up</span>
                  </div>
                  <div className="text-[11.5px] leading-snug text-muted">
                    Agents may only write bullets that point at one of these.
                  </div>
                </Card>
              </div>

              {/* Middle — experience */}
              <div className="flex flex-col gap-3" id="history">
                <Card elevation="sm">
                  <div className="flex items-baseline justify-between">
                    <SectionHead title="Experience" />
                    <Button variant="ghost" onClick={() => setExps([...exps, { title: '', company: '', location: '', start: '', end: '', bullets: [''] }])}>
                      + Add
                    </Button>
                  </div>
                  <div className="space-y-3">
                    {exps.map((e, i) => (
                      <div key={i} className="rounded-xl2 p-3" style={{ background: 'var(--color-bg)' }}>
                        <div className="grid gap-2 sm:grid-cols-2">
                          <Input placeholder="Title" value={e.title} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, title: ev.target.value }; setExps(c); }} />
                          <Input placeholder="Company" value={e.company} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, company: ev.target.value }; setExps(c); }} />
                          <Input placeholder="Location" value={e.location} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, location: ev.target.value }; setExps(c); }} />
                          <div className="grid grid-cols-2 gap-2">
                            <Input placeholder="Start" value={e.start} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, start: ev.target.value }; setExps(c); }} />
                            <Input placeholder="End" value={e.end} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, end: ev.target.value }; setExps(c); }} />
                          </div>
                        </div>
                        <Textarea
                          className="mt-2" rows={3} placeholder="Bullets — one per line" value={(e.bullets || []).join('\n')}
                          onChange={(ev) => { const c = [...exps]; c[i] = { ...e, bullets: ev.target.value.split('\n') }; setExps(c); }}
                        />
                        <button onClick={() => setExps(exps.filter((_, j) => j !== i))} className="mt-2 text-[11px] text-bad">Remove</button>
                      </div>
                    ))}
                    {exps.length === 0 && <p className="text-xs text-muted">No experience yet — paste a résumé or add one.</p>}
                  </div>
                </Card>

                {!!skillChips.length && (
                  <Card elevation="sm">
                    <div className="text-[11px] uppercase tracking-[0.09em] text-muted">Skills parsed</div>
                    <div className="flex flex-wrap gap-1.5">
                      {skillChips.map((s) => (
                        <Tag key={s} tone="neutral">
                          {s}
                          <button
                            type="button"
                            aria-label={`Remove ${s}`}
                            title="Remove — save to apply"
                            onClick={() => setSkills(skillChips.filter((x) => x !== s).join(', '))}
                            className="ml-1 opacity-60 hover:opacity-100"
                          >
                            ×
                          </button>
                        </Tag>
                      ))}
                    </div>
                    <p className="text-[11px] text-muted">Click × to drop a skill that was misread, then save.</p>
                  </Card>
                )}
              </div>

              {/* Right — live preview + coverage */}
              <div className="flex flex-col gap-3">
                <SectionHead title="Live preview" aside={name || 'Untitled'} />
                <Card elevation="lg" className="gap-1.5" style={{ background: 'var(--color-neutral-100)' }}>
                  <div className="border-b pb-2 text-center" style={{ borderColor: 'var(--color-divider)' }}>
                    <div style={{ fontFamily: 'var(--font-heading)', fontSize: 17 }}>{name || 'Your name'}</div>
                    <div className="mt-0.5 text-[10px] text-muted">
                      {[locations, links.github, phone].filter(Boolean).join(' · ') || 'Location · links · phone'}
                    </div>
                  </div>
                  <div className="mt-2 text-[9px] uppercase tracking-[0.12em]" style={{ color: 'var(--color-accent)' }}>
                    Experience
                  </div>
                  {exps.length === 0 && <p className="text-[10.5px] text-muted">Nothing entered yet.</p>}
                  {exps.slice(0, 2).map((e, i) => (
                    <div key={i} className="mt-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-[10.5px] font-semibold">{e.title || 'Title'}, {e.company || 'Company'}</span>
                        <span className="text-[9.5px] text-muted">{e.start}—{e.end}</span>
                      </div>
                      {(e.bullets || []).filter(Boolean).slice(0, 2).map((b, j) => (
                        <div key={j} className="text-[9.5px] leading-relaxed text-muted">{b}</div>
                      ))}
                    </div>
                  ))}
                  {!!skillChips.length && (
                    <>
                      <div className="mt-2 text-[9px] uppercase tracking-[0.12em]" style={{ color: 'var(--color-accent)' }}>
                        Skills
                      </div>
                      <div className="text-[9.5px] leading-relaxed text-muted">{skillChips.join(' · ')}</div>
                    </>
                  )}
                </Card>

                <Card elevation="sm">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[12.5px] font-semibold">Evidence coverage</span>
                    <span className="ml-auto" style={{ fontFamily: 'var(--font-heading)', fontSize: 20 }}>
                      {Math.round(coverage * 100)}%
                    </span>
                  </div>
                  <Meter value={coverage} />
                  <div className="text-[11.5px] leading-snug text-muted">
                    Share of the résumé bullets an agent has already tailored that trace back to a claim here.
                  </div>
                </Card>
              </div>
            </div>

            {/* Basics */}
            <Card elevation="sm" id="targets">
              <SectionHead title="Basics" />
              {placeholders.length > 0 && (
                <p className="rounded-[12px] px-3 py-2 text-xs"
                   style={{ background: 'color-mix(in srgb, var(--color-accent) 14%, transparent)' }}>
                  Your résumé file still has template blanks: <strong>{placeholders.join(', ')}</strong>.
                  They were left out — fill in the real values here (and in Experience / Education
                  below), or they'll be missing from every application.
                </p>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Full name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
                <Field label="Email"><Input value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
                <Field label="Phone"><Input value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
                <Field label="Target roles (comma-sep)"><Input value={roles} onChange={(e) => setRoles(e.target.value)} /></Field>
                <Field label="Locations (comma-sep)"><Input value={locations} onChange={(e) => setLocations(e.target.value)} /></Field>
                <Field label="Skills (comma-sep)"><Input value={skills} onChange={(e) => setSkills(e.target.value)} /></Field>
                <Field label="GitHub"><Input value={links.github || ''} onChange={(e) => setLinks({ ...links, github: e.target.value })} /></Field>
                <Field label="Website / LinkedIn"><Input value={links.website || links.linkedin || ''} onChange={(e) => setLinks({ ...links, website: e.target.value })} /></Field>
              </div>
            </Card>

            {/* Projects */}
            <Card elevation="sm">
              <div className="flex items-baseline justify-between">
                <SectionHead title="Projects" />
                <Button variant="ghost" onClick={() => setProjs([...projs, { name: '', link: '', bullets: [''] }])}>+ Add</Button>
              </div>
              <div className="space-y-3">
                {projs.map((p, i) => (
                  <div key={i} className="rounded-xl2 p-3" style={{ background: 'var(--color-bg)' }}>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <Input placeholder="Name" value={p.name} onChange={(ev) => { const c = [...projs]; c[i] = { ...p, name: ev.target.value }; setProjs(c); }} />
                      <Input placeholder="Link" value={p.link} onChange={(ev) => { const c = [...projs]; c[i] = { ...p, link: ev.target.value }; setProjs(c); }} />
                    </div>
                    <Textarea
                      className="mt-2" rows={2} placeholder="Bullets — one per line" value={(p.bullets || []).join('\n')}
                      onChange={(ev) => { const c = [...projs]; c[i] = { ...p, bullets: ev.target.value.split('\n') }; setProjs(c); }}
                    />
                    <button onClick={() => setProjs(projs.filter((_, j) => j !== i))} className="mt-2 text-[11px] text-bad">Remove</button>
                  </div>
                ))}
              </div>
            </Card>

            {/* Education */}
            <Card elevation="sm">
              <div className="flex items-baseline justify-between">
                <SectionHead title="Education" />
                <Button variant="ghost" onClick={() => setEdu([...edu, { school: '', degree: '', end: '' }])}>+ Add</Button>
              </div>
              <div className="space-y-3">
                {edu.map((e, i) => (
                  <div key={i} className="grid gap-2 sm:grid-cols-3">
                    <Input placeholder="School" value={e.school} onChange={(ev) => { const c = [...edu]; c[i] = { ...e, school: ev.target.value }; setEdu(c); }} />
                    <Input placeholder="Degree" value={e.degree} onChange={(ev) => { const c = [...edu]; c[i] = { ...e, degree: ev.target.value }; setEdu(c); }} />
                    <div className="flex gap-2">
                      <Input placeholder="Year" value={e.end} onChange={(ev) => { const c = [...edu]; c[i] = { ...e, end: ev.target.value }; setEdu(c); }} />
                      <button onClick={() => setEdu(edu.filter((_, j) => j !== i))} className="text-[11px] text-bad">✕</button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            <Card elevation="sm" id="boards">
              <SectionHead title="Search specific employers" />
              <p className="text-[11px] text-muted">
                Optional. We already search a curated set of public company boards. Add handles here to search particular employers as well — and note that Greenhouse and Lever are the two boards "Approve &amp; apply" can submit to directly.
              </p>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Greenhouse board tokens (comma-sep)"><Input value={ghTokens} onChange={(e) => setGhTokens(e.target.value)} /></Field>
                <Field label="Lever company slugs (comma-sep)"><Input value={leverSlugs} onChange={(e) => setLeverSlugs(e.target.value)} /></Field>
                <Field label="Ashby company slugs — discovery only"><Input value={ashbySlugs} onChange={(e) => setAshbySlugs(e.target.value)} /></Field>
              </div>
            </Card>

            <Card elevation="sm" id="screening">
              <SectionHead title="Screening answers" />
              <p className="text-[11px] text-muted">
                Optional. Used to auto-fill the screening questions on application forms — leave anything blank to skip it.
              </p>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {SCREENING_FIELDS.map((f) => {
                  const v = answerValue(f.key);
                  return (
                    <Field key={f.key} label={f.label}>
                      {f.yesNo ? (
                        <Select value={v} onChange={(e) => setAnswer(f.key, e.target.value)}>
                          <option value="">Not set</option>
                          <option value="Yes">Yes</option>
                          <option value="No">No</option>
                          {v && v !== 'Yes' && v !== 'No' && <option value={v}>{v}</option>}
                        </Select>
                      ) : (
                        <Input value={v} placeholder={f.placeholder} onChange={(e) => setAnswer(f.key, e.target.value)} />
                      )}
                    </Field>
                  );
                })}
              </div>
              <details>
                <summary className="cursor-pointer text-[12.5px] font-semibold">Voluntary self-identification</summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {SELF_ID_FIELDS.map((f) => (
                    <Field key={f.key} label={f.label}>
                      <Input
                        value={answerValue(f.key)}
                        placeholder="Decline to self-identify"
                        onChange={(e) => setAnswer(f.key, e.target.value)}
                      />
                    </Field>
                  ))}
                </div>
              </details>
            </Card>

            <div className="flex items-center gap-3">
              {saveButton}
              <Button variant="secondary" onClick={saveAndOpenDashboard} disabled={saving}>
                Save &amp; open dashboard →
              </Button>
              {msg && (
                <span className={`text-sm ${/fail|can't/i.test(msg) ? 'font-semibold text-[color:var(--color-accent-700)]' : 'text-muted'}`}>
                  {msg}
                </span>
              )}
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
