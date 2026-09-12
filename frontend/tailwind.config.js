/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 抖音式深色底座：不是纯黑，留一点蓝紫倾向，
        // 纯 #000 在 OLED 上边缘会「发脏」，用户截图里看得出来
        ink: {
          900: '#08080b',
          800: '#0f0f14',
          700: '#17171f',
          600: '#22222d',
        },
        // 强调色：GitHub 蓝 + 遗珠金
        // 金色专门留给「遗珠」标记 —— 这是本项目的核心差异点，
        // 用颜色把它做成品牌符号
        accent: {
          DEFAULT: '#3b82f6',
          glow: '#60a5fa',
        },
        gem: {
          DEFAULT: '#f5b942',
          soft: '#fcd34d',
        },
        risk: {
          safe: '#22c55e',
          caution: '#f59e0b',
          danger: '#ef4444',
        },
      },
      fontFamily: {
        sans: [
          '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"',
          '"PingFang SC"', '"Hiragino Sans GB"', '"Microsoft YaHei"',
          'sans-serif',
        ],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Consolas', 'monospace'],
      },
      backdropBlur: {
        glass: '24px',
      },
      boxShadow: {
        glass: '0 8px 32px rgba(0, 0, 0, 0.5)',
        gem: '0 0 24px rgba(245, 185, 66, 0.35)',
      },
      keyframes: {
        // 卡片入场：从下方轻微上滑 + 淡入
        slideUp: {
          '0%': { transform: 'translateY(16px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        // 遗珠标记的呼吸光晕
        gemPulse: {
          '0%, 100%': { opacity: '0.55' },
          '50%': { opacity: '1' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      animation: {
        'slide-up': 'slideUp 0.32s cubic-bezier(0.22, 1, 0.36, 1) both',
        'gem-pulse': 'gemPulse 2.4s ease-in-out infinite',
        shimmer: 'shimmer 1.6s linear infinite',
      },
    },
  },
  plugins: [],
}
