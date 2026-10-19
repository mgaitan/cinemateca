import type { Config } from 'tailwindcss'
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {
    colors: { paper: '#f5f4f0', ink: '#1b1b1a', muted: '#706f69', cobalt: '#214deb', accent: '#eee98a' },
    fontFamily: { sans: ['DM Sans', 'Arial', 'sans-serif'], display: ['DM Sans', 'Arial', 'sans-serif'] },
  }}, plugins: []
} satisfies Config
