/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // ── Apple 设计系统（design-md/apple/DESIGN.md）──
        //
        // 单一交互色 Action Blue：所有「可点」的元素只用这一种蓝。
        // 不存在第二种品牌色。
        accent: {
          DEFAULT: '#0066cc', // Action Blue —— 链接 / 胶囊 CTA / 焦点信号
          focus: '#0071e3',   // Focus Blue —— 键盘焦点环 / 按压态
          sky: '#2997ff',     // Sky Link Blue —— 暗面专用链接蓝（备用）
        },
        // 墨色阶梯：近黑 #1d1d1f 而非纯黑，页面更像「摄影」而非「印刷」。
        // 阶梯 400 / 600 / 700 的字重纪律见组件层。
        ink: {
          DEFAULT: '#1d1d1f', // 所有标题与正文
          soft: '#333333',    // ink-muted-80：次级正文
          muted: '#7a7a7a',   // ink-muted-48：辅助说明 / 法律细则
          faint: '#a1a1a6',   // 极弱：占位符、禁用态
        },
        // 遗珠金 —— 语义标记色（非交互元素专用），浅底下换深琥珀保证可读。
        // 蓝色永远只给「能点的东西」，金色只给「遗珠」这个品牌符号。
        gem: {
          DEFAULT: '#b45309',
          soft: '#d97706',
        },
        // 许可证风险（语义色，非交互）
        risk: {
          safe: '#1a7f37',
          caution: '#b45309',
          danger: '#d32f2f',
        },
        // 表面三档：白 / 羊皮纸 / 珍珠。颜色差本身就是分隔线。
        canvas: '#ffffff',
        parchment: '#f5f5f7',
        pearl: '#fafafc',
        // 近黑瓷面（暗色瓦片，供明暗节奏反转使用）
        tile: { 1: '#272729', 2: '#2a2a2c', 3: '#252527' },
        // 摄影上方悬浮圆形按钮的半透明灰基色（~64% alpha 使用）
        chip: '#d2d2d7',
        hairline: '#e0e0e0', // 工具卡 1px 发丝线
        divider: '#f0f0f0',  // 软分隔 / 进度条轨道
        void: '#000000',     // 全局导航级的「真黑」，默认不用
      },
      fontFamily: {
        sans: [
          '-apple-system', 'BlinkMacSystemFont', '"SF Pro Text"', '"SF Pro Display"',
          '"Segoe UI"', '"PingFang SC"', '"Hiragino Sans GB"', '"Microsoft YaHei"',
          'system-ui', 'sans-serif',
        ],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Consolas', 'monospace'],
      },
      borderRadius: {
        util: '8px',   // 紧凑工具按钮 / 内嵌小图
        pearl: '11px', // 珍珠胶囊小卡
        card: '18px',  // 白色工具卡
      },
      backdropBlur: {
        glass: '24px',
      },
      boxShadow: {
        // 全系统唯一的投影 —— 只给「搁在表面上的图像」，
        // 永远不给卡片、按钮、文字（Apple 阴影纪律）。
        product: '3px 5px 30px rgba(0, 0, 0, 0.22)',
      },
      keyframes: {
        // 卡片入场：从下方轻微上滑 + 淡入
        slideUp: {
          '0%': { transform: 'translateY(16px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        // 遗珠标记的呼吸（浅色下改为纯透明度呼吸，无光晕）
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
