import { fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import { describe, expect, it } from "vitest"

import { Slider } from "./slider"

function Harness() {
  const [value, setValue] = useState(10)
  return (
    <Slider
      aria-label="Example limit"
      min={1}
      max={20}
      value={value}
      onValueChange={setValue}
    />
  )
}

describe("Slider", () => {
  it("exposes an accessible shadcn slider and supports keyboard changes", () => {
    render(<Harness />)
    const slider = screen.getByRole("slider", { name: "Example limit" })
    expect(slider).toHaveValue("10")
    fireEvent.keyDown(slider, { key: "ArrowRight" })
    expect(slider).toHaveValue("11")
  })
})
