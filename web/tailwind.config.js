/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        'mv-bg': '#F7F7F6',
        'mv-surface': '#FFFFFF',
        'mv-surface-2': '#F1F1EF',
        'mv-border': '#E4E4E1',
        'mv-accent': '#0B0B0B',
        'mv-accent-hover': '#272727',
        'mv-text': '#0A0A0A',
        'mv-text-secondary': '#737373',
        'mv-text-muted': '#9A9A9A',
        'mv-fake': '#C83E56',
        'mv-real': '#20A464',
        'mv-uncertain': '#C58A17',
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
      },
      borderRadius: {
        DEFAULT: '8px',
        md: '12px',
        lg: '16px',
        xl: '24px',
      },
    },
  },
  plugins: [],
};
