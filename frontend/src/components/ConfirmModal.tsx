import { AlertTriangle, ShieldAlert, X } from 'lucide-react'
import { useEffect } from 'react'

interface ConfirmModalProps {
  isOpen: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  variant?: 'danger' | 'warning' | 'primary'
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmModal({
  isOpen,
  title,
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  variant = 'primary',
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  // Prevent body scroll when modal is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => {
      document.body.style.overflow = ''
    }
  }, [isOpen])

  if (!isOpen) return null

  const variantColors = {
    danger: {
      border: 'border-red-500/30',
      bg: 'bg-red-950/10',
      accent: 'text-red-400',
      btn: 'bg-red-500 text-white hover:bg-red-600 focus:ring-red-500/20',
      icon: ShieldAlert,
    },
    warning: {
      border: 'border-amber-500/30',
      bg: 'bg-amber-950/10',
      accent: 'text-amber-400',
      btn: 'bg-amber-500 text-black hover:bg-amber-600 focus:ring-amber-500/20',
      icon: AlertTriangle,
    },
    primary: {
      border: 'border-[#98e94d]/30',
      bg: 'bg-[#11170d]/30',
      accent: 'text-[#98e94d]',
      btn: 'bg-[#98e94d] text-black hover:bg-[#aef070] focus:ring-[#98e94d]/20',
      icon: ShieldAlert,
    },
  }

  const { border, bg: variantBg, accent, btn, icon: Icon } = variantColors[variant]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Blurred Backdrop */}
      <div 
        className="absolute inset-0 bg-black/70 backdrop-blur-md transition-opacity duration-300"
        onClick={onCancel}
      />

      {/* Modal Container */}
      <div 
        className={`relative w-full max-w-md rounded-2xl border ${border} bg-[#121210]/95 p-6 shadow-2xl backdrop-blur-xl transition-all duration-300 transform scale-100 animate-fade-in`}
        style={{
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(244, 241, 234, 0.03)'
        }}
        role="dialog"
        aria-modal="true"
      >
        {/* Decorative Top Glow */}
        <div 
          className={`absolute top-0 left-1/2 -translate-x-1/2 w-48 h-20 pointer-events-none blur-2xl rounded-full opacity-20 ${variantBg}`}
        />

        {/* Close Button */}
        <button
          onClick={onCancel}
          className="absolute top-4 right-4 p-1.5 rounded-lg border border-[#2b2a26] text-[#77736c] hover:text-[#f4f1ea] hover:bg-[#1c1b18] transition-colors cursor-pointer"
          aria-label="Close modal"
        >
          <X size={14} />
        </button>

        {/* Content */}
        <div className="flex gap-4 items-start">
          <div className={`p-3 rounded-xl border ${border} bg-[#090908] shrink-0 ${accent}`}>
            <Icon size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-bold text-[#f4f1ea] leading-snug">
              {title}
            </h2>
            <p className="text-sm text-[#9a968f] leading-relaxed mt-2 whitespace-pre-wrap">
              {message}
            </p>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3 items-center justify-end mt-6">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-xs font-semibold rounded-full border border-[#2b2a26] bg-[#141412] text-[#d8d3c8] hover:bg-[#1c1b18] hover:text-[#f4f1ea] transition-all cursor-pointer"
          >
            {cancelText}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={`px-5 py-2 text-xs font-bold rounded-full transition-all cursor-pointer shadow-lg ${btn}`}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
