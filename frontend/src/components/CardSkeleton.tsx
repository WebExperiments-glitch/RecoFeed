import type { FC } from 'react'

/**
 * Feed 卡片骨架屏。
 *
 * ⚠️ 骨架的版式要与真实卡片一致（同一个高度、同样的元素位置），
 *    否则数据到达时会「跳版」——这在全屏滑动里特别明显。
 */
const CardSkeleton: FC = () => (
  <div className="h-full w-full flex flex-col justify-between p-6 pt-14">
    <div className="space-y-4">
      {/* owner + name */}
      <div className="flex items-center gap-3">
        <div className="skeleton h-10 w-10 rounded-xl" />
        <div className="flex-1 space-y-2">
          <div className="skeleton h-4 w-40" />
          <div className="skeleton h-3 w-24" />
        </div>
      </div>
      {/* 推荐理由 */}
      <div className="skeleton h-6 w-52 rounded-full" />
      {/* 描述 */}
      <div className="space-y-2 pt-1">
        <div className="skeleton h-4 w-full" />
        <div className="skeleton h-4 w-[92%]" />
        <div className="skeleton h-4 w-[70%]" />
      </div>
      {/* 标签 */}
      <div className="flex gap-2 pt-1">
        <div className="skeleton h-6 w-16 rounded-full" />
        <div className="skeleton h-6 w-20 rounded-full" />
        <div className="skeleton h-6 w-14 rounded-full" />
      </div>
    </div>
    {/* 底部指标 */}
    <div className="flex gap-3">
      <div className="skeleton h-14 flex-1 rounded-2xl" />
      <div className="skeleton h-14 flex-1 rounded-2xl" />
      <div className="skeleton h-14 flex-1 rounded-2xl" />
    </div>
  </div>
)

export default CardSkeleton
