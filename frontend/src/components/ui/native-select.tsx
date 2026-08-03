import * as React from "react"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

type NativeSelectProps = Omit<React.ComponentProps<"select">, "size"> & {
  size?: "sm" | "default"
  variant?: "default" | "unstyled"
}

function NativeSelect({
  children,
  className,
  disabled,
  id,
  onChange,
  size = "default",
  value,
  defaultValue,
  ...props
}: NativeSelectProps) {
  const options = React.Children.toArray(children)
    .filter(React.isValidElement<React.ComponentProps<"option">>)
    .map((option) => ({
      disabled: option.props.disabled,
      label: option.props.children,
      value: String(option.props.value ?? option.props.children ?? ""),
    }))
  const selectedLabel = options.find(
    (option) => option.value === String(value ?? defaultValue ?? ""),
  )?.label

  return (
    <Select
      value={value == null ? undefined : String(value)}
      defaultValue={defaultValue == null ? undefined : String(defaultValue)}
      disabled={disabled}
      onValueChange={(nextValue) =>
        onChange?.({
          target: { value: nextValue },
          currentTarget: { value: nextValue },
        } as React.ChangeEvent<HTMLSelectElement>)
      }
    >
      <SelectTrigger
        id={id}
        size={size}
        aria-label={props["aria-label"]}
        className={cn("w-full", className)}
      >
        <SelectValue>{selectedLabel}</SelectValue>
      </SelectTrigger>
      <SelectContent align="start">
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export { NativeSelect }
