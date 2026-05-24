/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f0f4ff',
          500: '#4f6ef7',
          600: '#3b5bdb',
          900: '#1a237e',
        },
      },
    },
  },
  plugins: [],
}
