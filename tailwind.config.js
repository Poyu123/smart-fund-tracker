/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ["./基金查询.html"], // 告诉 Tailwind 扫描这个 HTML 文件里的类名
  theme: {
    extend: {
      colors: { 
        primary: '#165DFF', up: '#F53F3F', down: '#00B42A', 
        neutral: '#1D2129', light: '#86909C', bg: '#F7F8FA' 
      },
      fontFamily: { sans: ['Inter', 'Microsoft YaHei', 'sans-serif'] },
    },
  },
  plugins: [],
}
