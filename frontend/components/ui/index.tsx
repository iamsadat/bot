/* Grove primitives.
 *
 * Thin wrappers over the classes in app/grove.css, which is the ported design
 * system. The rule that keeps this layer honest: a primitive may compose the
 * system's classes and nothing else — no colours, radii, or shadows written
 * here. If a component needs a token the system does not have, add it to
 * grove.css so every consumer gets it, rather than inlining a one-off.
 */

import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react';

export const cx = (...parts: Array<string | false | null | undefined>) =>
  parts.filter(Boolean).join(' ');

/* ── buttons ─────────────────────────────────────────────────────────── */

type ButtonVariant = 'primary' | 'secondary' | 'ghost';

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  block?: boolean;
  icon?: boolean;
};

export function Button({
  variant = 'secondary',
  block,
  icon,
  className,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx('btn', `btn-${variant}`, block && 'btn-block', icon && 'btn-icon', className)}
      {...rest}
    />
  );
}

type LinkButtonProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  variant?: ButtonVariant;
  block?: boolean;
};

/** Same skin as Button, for things that are genuinely navigation. */
export function LinkButton({ variant = 'secondary', block, className, ...rest }: LinkButtonProps) {
  return <a className={cx('btn', `btn-${variant}`, block && 'btn-block', className)} {...rest} />;
}

/* ── tags ────────────────────────────────────────────────────────────── */

type TagTone = 'accent' | 'accent-2' | 'neutral' | 'outline' | 'solid';

export function Tag({
  tone = 'neutral',
  className,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: TagTone }) {
  return <span className={cx('tag', `tag-${tone}`, className)} {...rest} />;
}

/* ── surfaces ────────────────────────────────────────────────────────── */

type CardProps = HTMLAttributes<HTMLDivElement> & {
  elevation?: 'sm' | 'md' | 'lg' | 'none';
  lift?: boolean;
};

export function Card({ elevation = 'sm', lift, className, ...rest }: CardProps) {
  return (
    <div
      className={cx('card', elevation !== 'none' && `elev-${elevation}`, lift && 'lift', className)}
      {...rest}
    />
  );
}

export function CardKicker({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('card-kicker', className)} {...rest} />;
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('card-title', className)} {...rest} />;
}

export function CardMeta({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('card-meta', className)} {...rest} />;
}

/* ── stat ────────────────────────────────────────────────────────────── */

/** The headline figure pattern: display-font number over a quiet label. */
export function Stat({
  value,
  label,
  note,
  noteTone = 'muted',
  accent,
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & {
  value: ReactNode;
  label: ReactNode;
  note?: ReactNode;
  noteTone?: 'muted' | 'good' | 'inherit';
  /** Inverts the tile to the accent fill, as the mockup's Interviews tile does. */
  accent?: boolean;
}) {
  return (
    <div
      className={cx('card elev-sm lift', className)}
      style={
        accent ? { background: 'var(--color-accent)', color: 'var(--color-bg)' } : undefined
      }
      {...rest}
    >
      <div
        className="text-[10.5px] uppercase tracking-[0.09em]"
        style={{ color: accent ? undefined : 'var(--color-neutral-600)', opacity: accent ? 0.75 : 1 }}
      >
        {label}
      </div>
      <div className="flex items-baseline gap-2">
        <span
          className="text-[36px] leading-[1.2]"
          style={{ fontFamily: 'var(--font-heading)' }}
        >
          {value}
        </span>
        {note ? (
          <span
            className="text-xs"
            style={{
              color: accent
                ? undefined
                : noteTone === 'good'
                  ? 'var(--color-accent-2-700)'
                  : noteTone === 'muted'
                    ? 'var(--color-neutral-600)'
                    : undefined,
              fontWeight: noteTone === 'good' ? 600 : 400,
              opacity: accent ? 0.8 : 1,
            }}
          >
            {note}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/* ── meter ───────────────────────────────────────────────────────────── */

/** Horizontal bar for match scores and coverage. `value` is 0..1. */
export function Meter({
  value,
  tone = 'accent',
  className,
}: {
  value: number;
  tone?: 'accent' | 'good' | 'weak';
  className?: string;
}) {
  const pct = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0)) * 100;
  return (
    <div
      className={cx('meter', tone === 'good' && 'meter-good', tone === 'weak' && 'meter-weak', className)}
      role="meter"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ── forms ───────────────────────────────────────────────────────────── */

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx('field', className)}>
      <label>{label}</label>
      {children}
      {hint ? (
        <div className="mt-1 text-[11.5px]" style={{ color: 'var(--color-neutral-600)' }}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx('input', className)} {...rest} />;
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx('input', className)} {...rest} />;
}

export function Select({ className, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cx('input', className)} {...rest} />;
}

/* ── page furniture ──────────────────────────────────────────────────── */

/** Section heading used across the dashboard screens. */
export function SectionHead({
  title,
  aside,
  className,
}: {
  title: ReactNode;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx('flex items-baseline gap-2', className)}>
      <h4 className="m-0 text-[19px]">{title}</h4>
      {aside ? <span className="ml-auto text-xs text-muted">{aside}</span> : null}
    </div>
  );
}

/** Kicker + title + subtitle block at the top of a screen. */
export function PageHead({
  kicker,
  title,
  subtitle,
  actions,
}: {
  kicker?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="min-w-0 flex-1">
        {kicker ? (
          <div
            className="mb-1 text-[11px] uppercase tracking-[0.1em]"
            style={{ color: 'var(--color-accent)' }}
          >
            {kicker}
          </div>
        ) : null}
        <h2 className="m-0 text-[34px]">{title}</h2>
        {subtitle ? <div className="mt-1 text-sm text-muted">{subtitle}</div> : null}
      </div>
      {actions ? <div className="flex items-center gap-3">{actions}</div> : null}
    </div>
  );
}
