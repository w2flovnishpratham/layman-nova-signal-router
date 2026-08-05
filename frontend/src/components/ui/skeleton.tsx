import { cn } from "@/lib/utils"

/**
 * Loading placeholder for a single piece of data.
 *
 * Size it to the content it stands in for (`className="h-4 w-24"`), so the
 * placeholder occupies the same space the real value will — a skeleton that
 * doesn't match its content just moves the layout when data lands.
 *
 * Visual treatment lives in `.nova-skeleton` (index.css) rather than Tailwind
 * utilities: it needs a translucent white fill that stays visible on every
 * surface it sits on (page, card, nested panel) plus a sweep animation, and a
 * flat `bg-*` token can only be correct against one of those backgrounds.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("nova-skeleton", className)}
      {...props}
    />
  )
}

/**
 * A skeleton sized for a line of text. `lines={n}` renders a stack with the
 * last line short, the way a real paragraph wraps.
 */
function SkeletonText({
  lines = 1,
  className,
  ...props
}: React.ComponentProps<"div"> & { lines?: number }) {
  return (
    <div className={cn("grid gap-2", className)} {...props}>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={index}
          className={cn(
            "h-[0.85em] min-h-3 rounded",
            lines > 1 && index === lines - 1 ? "w-3/5" : "w-full",
          )}
        />
      ))}
    </div>
  )
}

export { Skeleton, SkeletonText }
