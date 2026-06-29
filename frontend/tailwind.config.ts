import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: '#06070d',
        surface: 'rgba(255,255,255,0.03)',
        ink: '#e7e9f3',
        muted: '#8c92a8',
        accent: '#6ea8fe',
        accent2: '#a78bfa',
        good: '#22d3a8',
        warn: '#fbbf24',
        bad: '#f87171',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: { xl2: '1.25rem' },
      boxShadow: {
        glow: '0 0 0 1px rgba(110,168,254,0.15), 0 20px 60px -20px rgba(110,168,254,0.35)',
        card: 'inset 0 1px 0 rgba(255,255,255,0.06), 0 18px 40px -24px rgba(0,0,0,0.8)',
      },
      backgroundImage: {
        grad: 'linear-gradient(135deg, #6ea8fe 0%, #a78bfa 100%)',
      },
      keyframes: {
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: { shimmer: 'shimmer 1.5s infinite' },
    },
  },
  plugins: [],
};

export default config;
