import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: '#f5ead8',
        surface: '#ebddc5',
        ink: '#201e1d',
        muted: '#82796a',
        accent: '#c67139',
        accent2: '#7a8a5e',
        good: '#56633f',
        warn: '#d67f48',
        bad: '#8c491a',
      },
      fontFamily: {
        sans: ['Figtree', 'system-ui', 'sans-serif'],
        display: ['Caprasimo', 'system-ui', 'serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: { xl2: '32px' },
      boxShadow: {
        glow: '0 12px 32px rgba(46,43,37,0.22)',
        card: '0 3px 10px rgba(46,43,37,0.16)',
      },
      backgroundImage: {
        grad: 'linear-gradient(135deg, #c67139 0%, #b2622d 100%)',
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
