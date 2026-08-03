import { Slider as SliderPrimitive } from "@base-ui/react/slider"

import { cn } from "@/lib/utils"

type SliderProps = SliderPrimitive.Root.Props<number> & {
  "aria-label": string
}

function Slider({
  className,
  "aria-label": ariaLabel,
  ...props
}: SliderProps) {
  return (
    <SliderPrimitive.Root
      data-slot="slider"
      className={cn(
        "relative flex w-full touch-none items-center select-none data-disabled:cursor-not-allowed data-disabled:opacity-50",
        className
      )}
      {...props}
    >
      <SliderPrimitive.Control
        data-slot="slider-control"
        className="flex w-full touch-none items-center py-2"
      >
        <SliderPrimitive.Track
          data-slot="slider-track"
          className="relative h-1 w-full grow overflow-hidden rounded-full bg-muted"
        >
          <SliderPrimitive.Indicator
            data-slot="slider-indicator"
            className="absolute h-full bg-primary"
          />
        </SliderPrimitive.Track>
        <SliderPrimitive.Thumb
          data-slot="slider-thumb"
          getAriaLabel={() => ariaLabel}
          className="block size-4 shrink-0 rounded-full border border-white bg-white shadow-sm outline-none transition-[color,box-shadow] hover:ring-4 hover:ring-ring/20 focus-visible:ring-4 focus-visible:ring-ring/35"
        />
      </SliderPrimitive.Control>
    </SliderPrimitive.Root>
  )
}

export { Slider }
