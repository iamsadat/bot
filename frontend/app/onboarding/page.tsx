'use client';

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import Nav from '@/components/Nav';
import { api } from '@/lib/api';

type Exp = { title: string; company: string; location: string; start: string; end: string; bullets: string[] };
type Edu = { school: string; degree: string; end: string };
type Proj = { name: string; link: string; bullets: string[] };

const inp = 'w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-ink placeholder:text-muted/50';
const card = 'glass rounded-xl2 p-4 shadow-card';

function Field({ label, ...p }: any) {
  return (
    <label className="block text-[11px] text-muted">
      {label}
      <input className={`${inp} mt-1`} {...p} />
    </label>
  );
}

export default function Onboarding() {
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

  useEffect(() => {
    api.profile().then((r) => {
      const p = r.profile;
      if (!p) return;
      setName(p.name || ''); setEmail(p.email || ''); setPhone(p.phone || '');
      setSkills((p.skills || []).join(', '));
      setExps((p.experiences || []) as Exp[]);
      setEdu((p.education || []) as Edu[]);
      setProjs((p.projects || []) as Proj[]);
      setLinks(p.links || {});
    }).catch(() => {});
  }, []);

  const applyParsed = (r: any) => {
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

  const importGithub = async () => {
    const u = (links.github || '').replace(/^https?:\/\/github\.com\//, '').replace(/\/$/, '');
    if (!u) { setMsg('Enter your GitHub (in Links) first.'); return; }
    setMsg('Importing GitHub…');
    try {
      const r = await api.importGithub(u);
      if (r.projects?.length) setProjs(r.projects as Proj[]);
      setMsg(`Imported ${r.added} project(s) from GitHub.`);
    } catch {
      setMsg('GitHub import failed.');
    }
  };

  const save = async () => {
    setMsg('Saving…');
    try {
      await api.saveProfile({
        name, email, phone,
        target_roles: roles.split(',').map((x) => x.trim()).filter(Boolean),
        locations: locations.split(',').map((x) => x.trim()).filter(Boolean),
        skills: skills.split(',').map((x) => x.trim()).filter(Boolean),
        links,
      });
      await api.saveStructured({ experiences: exps, education: edu, projects: projs, links });
      setMsg('Saved ✓');
    } catch {
      setMsg('Save failed — check name/email/role.');
    }
  };

  return (
    <main className="relative z-10 mx-auto min-h-screen max-w-4xl">
      <Nav right={<button onClick={save} className="rounded-full bg-grad px-4 py-2 text-sm font-semibold text-bg shadow-glow">Save profile</button>} />

      <div className="space-y-4 px-6 pb-16">
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className={card}>
          <h2 className="mb-3 text-sm font-semibold">Paste your résumé to auto-fill</h2>
          <textarea
            value={paste} onChange={(e) => setPaste(e.target.value)}
            rows={5} placeholder="Paste your résumé text — we'll extract experience, education, projects & skills."
            className={inp}
          />
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <button onClick={parse} className="glass rounded-lg px-4 py-2 text-sm font-medium hover:border-white/20">Parse & prefill</button>
            <label className="glass cursor-pointer rounded-lg px-4 py-2 text-sm font-medium hover:border-white/20">
              Upload .pdf/.docx
              <input type="file" accept=".pdf,.docx,.txt" className="hidden"
                onChange={(e) => onFile(e.target.files?.[0])} />
            </label>
            <button onClick={importGithub} className="glass rounded-lg px-4 py-2 text-sm font-medium hover:border-white/20">Import GitHub projects</button>
            {msg && <span className="text-xs text-muted">{msg}</span>}
          </div>
        </motion.div>

        <div className={card}>
          <h2 className="mb-3 text-sm font-semibold">Basics</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Full name" value={name} onChange={(e: any) => setName(e.target.value)} />
            <Field label="Email" value={email} onChange={(e: any) => setEmail(e.target.value)} />
            <Field label="Phone" value={phone} onChange={(e: any) => setPhone(e.target.value)} />
            <Field label="Target roles (comma-sep)" value={roles} onChange={(e: any) => setRoles(e.target.value)} />
            <Field label="Locations (comma-sep)" value={locations} onChange={(e: any) => setLocations(e.target.value)} />
            <Field label="Skills (comma-sep)" value={skills} onChange={(e: any) => setSkills(e.target.value)} />
            <Field label="GitHub" value={links.github || ''} onChange={(e: any) => setLinks({ ...links, github: e.target.value })} />
            <Field label="Website / LinkedIn" value={links.website || links.linkedin || ''} onChange={(e: any) => setLinks({ ...links, website: e.target.value })} />
          </div>
        </div>

        {/* Experience */}
        <div className={card}>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Experience</h2>
            <button onClick={() => setExps([...exps, { title: '', company: '', location: '', start: '', end: '', bullets: [''] }])} className="glass rounded-lg px-3 py-1 text-xs">+ Add</button>
          </div>
          <div className="space-y-3">
            {exps.map((e, i) => (
              <div key={i} className="rounded-lg border border-white/8 bg-white/[0.02] p-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  <input className={inp} placeholder="Title" value={e.title} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, title: ev.target.value }; setExps(c); }} />
                  <input className={inp} placeholder="Company" value={e.company} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, company: ev.target.value }; setExps(c); }} />
                  <input className={inp} placeholder="Location" value={e.location} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, location: ev.target.value }; setExps(c); }} />
                  <div className="grid grid-cols-2 gap-2">
                    <input className={inp} placeholder="Start" value={e.start} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, start: ev.target.value }; setExps(c); }} />
                    <input className={inp} placeholder="End" value={e.end} onChange={(ev) => { const c = [...exps]; c[i] = { ...e, end: ev.target.value }; setExps(c); }} />
                  </div>
                </div>
                <textarea className={`${inp} mt-2`} rows={3} placeholder="Bullets — one per line" value={(e.bullets || []).join('\n')}
                  onChange={(ev) => { const c = [...exps]; c[i] = { ...e, bullets: ev.target.value.split('\n') }; setExps(c); }} />
                <button onClick={() => setExps(exps.filter((_, j) => j !== i))} className="mt-2 text-[11px] text-bad">Remove</button>
              </div>
            ))}
            {exps.length === 0 && <p className="text-xs text-muted">No experience yet — paste a résumé or add one.</p>}
          </div>
        </div>

        {/* Projects */}
        <div className={card}>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Projects</h2>
            <button onClick={() => setProjs([...projs, { name: '', link: '', bullets: [''] }])} className="glass rounded-lg px-3 py-1 text-xs">+ Add</button>
          </div>
          <div className="space-y-3">
            {projs.map((p, i) => (
              <div key={i} className="rounded-lg border border-white/8 bg-white/[0.02] p-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  <input className={inp} placeholder="Name" value={p.name} onChange={(ev) => { const c = [...projs]; c[i] = { ...p, name: ev.target.value }; setProjs(c); }} />
                  <input className={inp} placeholder="Link" value={p.link} onChange={(ev) => { const c = [...projs]; c[i] = { ...p, link: ev.target.value }; setProjs(c); }} />
                </div>
                <textarea className={`${inp} mt-2`} rows={2} placeholder="Bullets — one per line" value={(p.bullets || []).join('\n')}
                  onChange={(ev) => { const c = [...projs]; c[i] = { ...p, bullets: ev.target.value.split('\n') }; setProjs(c); }} />
                <button onClick={() => setProjs(projs.filter((_, j) => j !== i))} className="mt-2 text-[11px] text-bad">Remove</button>
              </div>
            ))}
          </div>
        </div>

        {/* Education */}
        <div className={card}>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Education</h2>
            <button onClick={() => setEdu([...edu, { school: '', degree: '', end: '' }])} className="glass rounded-lg px-3 py-1 text-xs">+ Add</button>
          </div>
          <div className="space-y-3">
            {edu.map((e, i) => (
              <div key={i} className="grid gap-2 sm:grid-cols-3">
                <input className={inp} placeholder="School" value={e.school} onChange={(ev) => { const c = [...edu]; c[i] = { ...e, school: ev.target.value }; setEdu(c); }} />
                <input className={inp} placeholder="Degree" value={e.degree} onChange={(ev) => { const c = [...edu]; c[i] = { ...e, degree: ev.target.value }; setEdu(c); }} />
                <div className="flex gap-2">
                  <input className={inp} placeholder="Year" value={e.end} onChange={(ev) => { const c = [...edu]; c[i] = { ...e, end: ev.target.value }; setEdu(c); }} />
                  <button onClick={() => setEdu(edu.filter((_, j) => j !== i))} className="text-[11px] text-bad">✕</button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button onClick={save} className="rounded-full bg-grad px-6 py-2.5 font-semibold text-bg shadow-glow">Save profile</button>
          <a href="/dashboard" className="glass rounded-full px-6 py-2.5 font-medium">Go to dashboard →</a>
          {msg && <span className="text-xs text-muted">{msg}</span>}
        </div>
      </div>
    </main>
  );
}
