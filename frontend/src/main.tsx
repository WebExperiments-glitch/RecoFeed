import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from '@/App'
import '@/styles/index.css'

const el = document.getElementById('root')
if (!el) throw new Error('#root 未找到，检查 index.html')

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
