import ReactMarkdown from 'react-markdown'
import type { FC } from 'react'
import remarkGfm from 'remark-gfm'

interface Props {
  /** README 原文（markdown） */
  markdown: string
}

/** 徽章图（shields.io 等）在抽屉里只会糊成一片，直接不渲染 */
const BADGE_HINT = /badge|shields\.io|badgen|codecov|travis|circleci|appveyor|actions\/workflows/i

/**
 * README 全文渲染。
 *
 * 用户的批评很直接：「它把这么丰富的一个项目，简化成了 1 Star、0 Fork、0 Issue
 * 和一行没有标点的摘要」—— 卡片只能给"要不要点进去"的判据，
 * 真正判断项目深度（架构、用法、文档质量）必须能看到**完整 README**。
 * 所以抽屉里按 markdown 正常排版，而不是塞进 <pre> 当纯文本。
 */
const ReadmeView: FC<Props> = ({ markdown }) => (
  <div className="readme-md">
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => (
          <h1 className="text-[19px] font-semibold text-ink tracking-[-0.45px] mt-6 mb-2.5 pb-1.5
                         border-b border-divider first:mt-0">
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="text-[16.5px] font-semibold text-ink tracking-[-0.35px] mt-5 mb-2 pb-1
                         border-b border-divider first:mt-0">
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3 className="text-[14.5px] font-semibold text-ink mt-4 mb-1.5">{children}</h3>
        ),
        h4: ({ children }) => (
          <h4 className="text-[13.5px] font-semibold text-ink-soft mt-3 mb-1.5">{children}</h4>
        ),
        p: ({ children }) => (
          <p className="text-[13px] leading-[1.8] text-ink-soft my-2.5">{children}</p>
        ),
        a: ({ children, href }) => (
          <a
            className="text-accent hover:underline break-all"
            href={href}
            target="_blank"
            rel="noreferrer"
          >
            {children}
          </a>
        ),
        ul: ({ children }) => (
          <ul className="list-disc pl-5 my-2.5 space-y-1 text-[13px] leading-[1.75] text-ink-soft">
            {children}
          </ul>
        ),
        ol: ({ children }) => (
          <ol className="list-decimal pl-5 my-2.5 space-y-1 text-[13px] leading-[1.75] text-ink-soft">
            {children}
          </ol>
        ),
        li: ({ children }) => <li className="pl-0.5">{children}</li>,
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-hairline pl-3 my-3 text-ink-muted text-[12.5px]
                                 leading-[1.75]">
            {children}
          </blockquote>
        ),
        code: ({ children, className }) => {
          const isBlock = Boolean(className?.includes('language-'))
          if (isBlock) {
            return (
              <code className="block font-mono text-[12px] leading-[1.7] text-ink-soft
                               whitespace-pre overflow-x-auto">
                {children}
              </code>
            )
          }
          return (
            <code className="font-mono text-[12px] bg-parchment border border-hairline
                             rounded-[4px] px-1 py-[1px] text-ink-soft">
              {children}
            </code>
          )
        },
        pre: ({ children }) => (
          <pre className="bg-parchment border border-hairline rounded-pearl p-3 my-3 overflow-x-auto">
            {children}
          </pre>
        ),
        table: ({ children }) => (
          <div className="my-3 overflow-x-auto">
            <table className="w-full text-[12.5px] border-collapse">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="border border-hairline bg-parchment px-2 py-1.5 text-left font-semibold
                         text-ink">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="border border-hairline px-2 py-1.5 text-ink-soft align-top">{children}</td>
        ),
        hr: () => <hr className="border-0 border-t border-divider my-4" />,
        img: ({ src, alt }) => {
          const url = String(src ?? '')
          // 徽章/CI 图不渲染：它们在抽屉里是纯噪声（一屏全是 gray 小图）
          if (!url || BADGE_HINT.test(url)) return null
          return (
            <img
              className="max-w-full rounded-pearl border border-hairline my-3"
              src={url}
              alt={alt ?? ''}
              loading="lazy"
            />
          )
        },
      }}
    >
      {markdown}
    </ReactMarkdown>
  </div>
)

export default ReadmeView
