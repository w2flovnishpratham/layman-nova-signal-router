import { type InputHTMLAttributes, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'

type SecretInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  revealLabel?: string
}

export default function SecretInput({ className = 'input', revealLabel = 'secret', disabled, ...props }: SecretInputProps) {
  const [revealed, setRevealed] = useState(false)
  const inputClassName = `${className} pr-11`
  const title = `Hold to reveal ${revealLabel}`

  return (
    <div className="relative">
      <input
        {...props}
        className={inputClassName}
        disabled={disabled}
        type={revealed ? 'text' : 'password'}
      />
      <button
        aria-label={title}
        aria-pressed={revealed}
        className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-[#77736c] transition-all duration-150 hover:bg-[#24231f] hover:text-[#f4f1ea] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#98e94d] disabled:cursor-not-allowed disabled:opacity-40"
        disabled={disabled}
        onBlur={() => setRevealed(false)}
        onKeyDown={(event) => {
          if (event.key === ' ' || event.key === 'Enter') {
            event.preventDefault()
            setRevealed(true)
          }
        }}
        onKeyUp={() => setRevealed(false)}
        onPointerCancel={() => setRevealed(false)}
        onPointerDown={(event) => {
          event.preventDefault()
          setRevealed(true)
        }}
        onPointerLeave={() => setRevealed(false)}
        onPointerUp={() => setRevealed(false)}
        title={title}
        type="button"
      >
        {revealed ? (
          <Eye className="scale-110 text-[#98e94d] transition-all duration-150" size={16} />
        ) : (
          <EyeOff className="transition-all duration-150" size={16} />
        )}
      </button>
    </div>
  )
}
